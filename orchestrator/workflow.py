"""Ticket state machine: plan -> assign -> build -> review -> test -> human merge.

GitHub issue labels are the source of truth for ticket state; every transition is posted as
an agent-attributed comment carrying a machine-readable `newsroom-event` marker, and is
appended to the local JSONL ledger with token usage.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
import time
import uuid
from pathlib import Path

from . import config, ledger, runtimes, workspace
from .gh import GitHub, push_branch
from .ledger import Event

DEPENDS_RE = re.compile(r"^Depends on:\s*(.*)$", re.MULTILINE | re.IGNORECASE)
MAX_DIFF_CHARS = 60_000
MAX_FEEDBACK_CHARS = 16_000
# OpenClaw has no per-run turn limit, so a turn budget is enforced approximately: the agent is
# told its budget and the run gets a wall-clock deadline of SECONDS_PER_TURN per budgeted turn.
SECONDS_PER_TURN = 6
# Tester-written acceptance tests are committed here, one file per ticket. The builder may not
# change or delete them (enforced when importing the builder's files).
ACCEPTANCE_DIR = "app/tests/acceptance/"
PUBLISHER = "newsroom-orchestrator (harness)"
TEST_TIMEOUT_S = 300

PLAN_PROMPT = """\
Break the product brief below into implementation tickets for the builder agent.

Return ONLY a JSON object of this shape:
{{"tickets": [{{"key": "T1", "title": "...", "description": "...",
  "acceptance_criteria": ["...", "..."], "depends_on": ["T0"], "files": ["app/newsroom/..."]}}]}}

Rules:
- 4 to 7 tickets, ordered so dependencies come first. Core requirements only; no nice-to-haves.
- Each ticket is one pull request a small coding model can finish in one pass.
- Acceptance criteria must be checkable by offline pytest (HTTP mocked, temporary SQLite).
- Follow the brief's delivery order. Include the scheduled refresh as a ticket.
- Deployment (server, hosting) is done by the human engineer; do not create tickets for it.

PRODUCT BRIEF:
{brief}
"""

ASSIGN_PROMPT = """\
You are assigning the next ticket to the builder agent.

Board (only tickets whose dependencies are merged are listed as candidates):
{board}

Full text of each candidate ticket (its acceptance criteria are binding):
{details}

Feedback on candidates that were sent back by review or test:
{feedback}

Return ONLY a JSON object:
{{"ticket": <issue number from the candidates>, "reason": "<one sentence>",
  "instructions": ["<what the builder must do or fix, at most 5 bullet points>"]}}

Instructions must be consistent with the chosen ticket's acceptance criteria and file list.
Do not invent requirements, file paths or providers that the ticket does not state.
"""

REVIEW_PROMPT = """\
Review pull request #{pr} against its ticket.

TICKET #{ticket}: {title}
{body}

ENGINEERING CONSTRAINTS (from the brief):
{constraints}

DIFF (application code and the tester's acceptance tests under app/tests/acceptance/):
{diff}

TEST RESULTS (offline pytest on this exact commit, run by the harness):
{tests}

Check the application code against the ticket, and check that the acceptance tests really
exercise the acceptance criteria (not stubs that replace the code under test).

Return ONLY a JSON object:
{{"verdict": "approve" | "request_changes", "summary": "<one sentence>",
  "findings": [{{"file": "...", "problem": "...", "fix": "..."}}]}}
"""

BUILD_MESSAGE = """\
New assignment from dispatch: ticket #{ticket} (attempt {attempt} of {max_attempts}).
Read TICKET.md and BRIEF.md in your workspace, implement the ticket under repo/app/, then reply
with your summary.
"""

TEST_MESSAGE = """\
New handoff from reviewer: pull request #{pr} for ticket #{ticket} passed review.
Read TICKET.md, read the implementation under repo/app/, and write acceptance/test_acceptance.py.
Earlier tickets' acceptance tests are in repo/app/tests/acceptance/. If one of them fails only because
this ticket legitimately extends behaviour (for example, it adds database columns), you may minimally
update that file in place so it still checks its own ticket's acceptance criteria. Never weaken a real
requirement, and list any such change in your reply.
"""


class Orchestrator:
    def __init__(self, team: config.Team | None = None, github: GitHub | None = None):
        self.team = team or config.load_team()
        self.gh = github or GitHub(self.team)

    # -- helpers ----------------------------------------------------------------------------
    def _hermes(self, agent: str, prompt: str, ticket: int | None = None) -> runtimes.AgentReply:
        spec = self.team.agent(agent)
        reply = runtimes.run_hermes(spec["profile"], prompt, toolsets=spec.get("toolsets", "todo"))
        return self._require_allowed_provider(agent, reply, ticket)

    def _openclaw(self, agent: str, message: str, session: str, ticket: int | None = None,
                  max_turns: int | None = None, model: str | None = None) -> runtimes.AgentReply:
        timeout = 900
        if max_turns:
            message += (f"\n\nBudget: about {max_turns} tool calls in total. Plan before acting, do not re-read "
                        "files you have already read, and finish with your summary before the budget runs out.")
            timeout = max(60, max_turns * SECONDS_PER_TURN)
        reply = runtimes.run_openclaw(self.team.agent(agent)["agent_id"], message, session=session,
                                      timeout_s=timeout, model=model)
        return self._require_allowed_provider(agent, reply, ticket)

    def _require_allowed_provider(self, agent: str, reply: runtimes.AgentReply, ticket: int | None) -> runtimes.AgentReply:
        allowed = self.team.allowed_providers
        if allowed and reply.ok and reply.usage.provider not in allowed:
            self._record(Event(event="provider-refused", agent=agent, runtime=self._runtime(agent), ticket=ticket,
                               outcome="refused", usage=reply.usage, duration_s=reply.duration_s,
                               detail=f"reply served by {reply.usage.provider}/{reply.usage.model}; allowed {allowed}"))
            raise runtimes.AgentError(
                f"{agent} was answered by {reply.usage.provider}/{reply.usage.model}, not an allowed cloud provider "
                f"{allowed}; cloud inference is probably unavailable. Stopping instead of using a local model.")
        return reply

    def _runtime(self, agent: str) -> str:
        return self.team.agent(agent)["runtime"]

    def _header(self, agent: str, reply: runtimes.AgentReply | None = None) -> str:
        model = ""
        if reply is not None and reply.usage.model:
            model = f" · {reply.usage.provider}/{reply.usage.model}"
        return f"**[{agent} · {self._runtime(agent)}{model}]**"

    def _record(self, ev: Event, comment_on: list[int] | None = None, body: str | None = None,
                as_agent: str | None = None) -> None:
        ledger.append(ev)
        if comment_on and body:
            marker = {"event": ev.event, "agent": ev.agent, "ticket": ev.ticket, "pr": ev.pr,
                      "attempt": ev.attempt, "outcome": ev.outcome}
            if ev.usage is not None:
                marker["tokens"] = ev.usage.total_tokens
                marker["model"] = ev.usage.model
            for number in comment_on:
                self.gh.comment(as_agent or ev.agent, number, body, event=marker)

    @staticmethod
    def _phase(base: str, attempt: int) -> str:
        return base if attempt <= 1 else f"retry-{base}"

    @staticmethod
    def depends_on(body: str) -> list[int]:
        m = DEPENDS_RE.search(body or "")
        return [int(n) for n in re.findall(r"#(\d+)", m.group(1))] if m else []

    def _attempts(self, ticket: int) -> int:
        return sum(1 for e in self.gh.events("dispatch", ticket) if e.get("event") == "build")

    def _status(self, issue: dict) -> str | None:
        return next((l for l in issue["labels"] if l.startswith("status:")), None)

    def _branch(self, ticket: int) -> str:
        return f"ticket/{ticket}"

    def _constraints(self) -> str:
        brief = config.BRIEF_FILE.read_text()
        idx = brief.find("## Engineering constraints")
        return brief[idx:] if idx != -1 else brief

    # -- plan -------------------------------------------------------------------------------
    def plan(self) -> list[int]:
        existing = self.gh.list_tickets("dispatch")
        if existing:
            return [t["number"] for t in existing]
        reply = self._hermes("dispatch", PLAN_PROMPT.format(brief=config.BRIEF_FILE.read_text()))
        try:
            if not reply.ok:
                raise runtimes.AgentError(f"dispatch planning failed: {reply.error}")
            tickets = validate_plan(runtimes.extract_json(reply.text))
        except runtimes.AgentError as exc:
            self._record(Event(event="plan", agent="dispatch", runtime="hermes", phase="planning",
                               outcome="failed", usage=reply.usage, duration_s=reply.duration_s,
                               detail=f"{exc}; reply: {reply.text[:300]}"))
            raise
        self.gh.ensure_labels("dispatch")
        numbers: dict[str, int] = {}
        for t in tickets:
            deps = [f"#{numbers[d]}" for d in t["depends_on"] if d in numbers]
            body = (
                f"{t['description']}\n\n### Acceptance criteria\n"
                + "\n".join(f"- [ ] {c}" for c in t["acceptance_criteria"])
                + ("\n\n### Files\n" + "\n".join(f"- `{f}`" for f in t["files"]) if t["files"] else "")
                + f"\n\nDepends on: {', '.join(deps) if deps else 'none'}\n\n"
                f"_Created by **dispatch** (Hermes) from `agents/shared/brief.md`, plan key {t['key']}._"
            )
            numbers[t["key"]] = self.gh.create_issue("dispatch", t["title"], body, ["ticket", "status:todo"])
        self._record(Event(event="plan", agent="dispatch", runtime="hermes", phase="planning",
                           outcome="ok", usage=reply.usage, duration_s=reply.duration_s,
                           detail=f"created tickets {sorted(numbers.values())}"))
        return sorted(numbers.values())

    # -- assign -----------------------------------------------------------------------------
    def assign(self) -> int | None:
        tickets = self.gh.list_tickets("dispatch")
        closed = {t["number"] for t in tickets if t["state"] == "CLOSED"}
        candidates = [
            t for t in tickets
            if t["state"] == "OPEN"
            and self._status(t) in ("status:todo", "status:changes-requested")
            and all(d in closed for d in self.depends_on(t["body"]))
        ]
        if not candidates:
            return None
        board = "\n".join(
            f"- #{t['number']} [{self._status(t) or 'none'}] {t['title']}"
            + (" (CANDIDATE)" if t in candidates else "")
            for t in tickets if t["state"] == "OPEN"
        )
        feedback = self._feedback_for(candidates)
        details = "\n\n".join(f"#{t['number']}: {t['title']}\n{(t.get('body') or '')[:4000]}" for t in candidates)
        prompt = ASSIGN_PROMPT.format(board=board, details=details, feedback=feedback or "none")
        valid = {t["number"] for t in candidates}
        for ask in range(2):
            reply = self._hermes("dispatch", prompt)
            if not reply.ok:
                raise runtimes.AgentError(f"dispatch assignment failed: {reply.error}")
            try:
                choice = runtimes.extract_json(reply.text)
                number = int(choice.get("ticket", -1))
            except (runtimes.AgentError, TypeError, ValueError):
                choice, number = {}, -1
            if number in valid:
                break
            # Dispatch picked something illegal (dependency not merged, unknown issue, no JSON).
            # Record it and ask once more with the error; the harness never picks on its behalf.
            self._record(Event(event="assign", agent="dispatch", runtime="hermes", phase="planning",
                               outcome="invalid-choice", usage=reply.usage, duration_s=reply.duration_s,
                               detail=f"chose {number}, valid {sorted(valid)}; reply: {reply.text[:300]}"))
            prompt += (f"\n\nYour previous answer was invalid: {reply.text[:300]!r}. "
                       f"Answer with JSON whose \"ticket\" is one of {sorted(valid)}.")
        else:
            raise runtimes.AgentError(f"dispatch chose ticket {number}, not one of {sorted(valid)}")
        attempt = self._attempts(number) + 1
        instructions = [str(i) for i in choice.get("instructions") or []][:5]
        body = (
            f"{self._header('dispatch', reply)} **dispatch → builder**: assigned #{number} "
            f"(attempt {attempt}/{self.team.max_attempts}).\n\n> {choice.get('reason', '')}\n\n"
            + ("Instructions:\n" + "\n".join(f"- {i}" for i in instructions) if instructions else "")
        )
        self.gh.set_status_label("dispatch", number, "status:assigned")
        ev = Event(event="assign", agent="dispatch", runtime="hermes", ticket=number, attempt=attempt,
                   phase="planning", outcome="assigned:builder", usage=reply.usage, duration_s=reply.duration_s,
                   detail=json.dumps({"instructions": instructions}))
        self._record(ev, comment_on=[number], body=body)
        return number

    def _feedback_for(self, candidates: list[dict]) -> str:
        parts = []
        for t in candidates:
            if self._status(t) != "status:changes-requested":
                continue
            pr = self.gh.find_pr("dispatch", self._branch(t["number"]), open_only=True)
            if pr:
                rejects = [e for e in self.gh.events("dispatch", pr["number"]) if e.get("outcome") in ("request_changes", "fail")]
                notes = self._latest_rejection_text(pr["number"])
                parts.append(f"#{t['number']} (PR #{pr['number']}, {len(rejects)} rejection(s)):\n{notes}")
        return "\n\n".join(parts)

    def _latest_rejection_text(self, pr_number: int) -> str:
        # Only the team's own comments, identified by their trailing event marker. Text from
        # other accounts never reaches the builder (it would be a prompt-injection channel).
        for c in reversed(self.gh.team_comments("dispatch", pr_number)):
            if (c["event"] or {}).get("outcome") in ("request_changes", "fail"):
                return c["body"][:MAX_FEEDBACK_CHARS]
        return ""

    # -- build ------------------------------------------------------------------------------
    def build(self, ticket: int, max_turns: int | None = None) -> int | None:
        issue = self.gh.get_issue("builder", ticket)
        attempt = self._attempts(ticket) + 1
        if attempt > self.team.max_attempts:
            return self.escalate(ticket, f"builder exhausted {self.team.max_attempts} attempts")
        branch = self._branch(ticket)
        repo = workspace.harness_clone(self.team, "builder", "builder")
        start = f"origin/{branch}" if workspace.remote_branch_exists(repo, branch) else f"origin/{self.team.base_branch}"
        workspace.checkout(repo, branch, start)

        ws = config.WORK_DIR / "builder"
        workspace.export_tree(repo, ws / "repo")
        feedback = ""
        pr = self.gh.find_pr("builder", branch, open_only=True)
        if attempt > 1 and pr:
            feedback = self._latest_rejection_text(pr["number"])
        instructions = self._latest_assign_instructions(ticket)
        workspace.safe_write(ws / "TICKET.md", render_ticket(issue, instructions, feedback, attempt))
        workspace.safe_write(ws / "BRIEF.md", config.BRIEF_FILE.read_text())

        reply = self._openclaw(
            "builder",
            BUILD_MESSAGE.format(ticket=ticket, attempt=attempt, max_attempts=self.team.max_attempts),
            session=f"ticket-{ticket}-build-{attempt}", ticket=ticket, max_turns=max_turns,
        )
        changed, outside = workspace.import_changes(ws / "repo", repo, self.team.allowed_paths,
                                                    protected=[ACCEPTANCE_DIR])
        phase = self._phase("coding", attempt)
        # A run stopped by its turn budget may still have produced a usable change; the
        # reviewer and tester gate it like any other commit.
        budget_note = "" if reply.ok else f" (run ended early: {reply.error[:200]})"
        if not changed:
            reason = reply.error if not reply.ok else "builder produced no changes under app/"
            self.gh.set_status_label("builder", ticket, "status:changes-requested")
            self._record(
                Event(event="build", agent="builder", runtime="openclaw", ticket=ticket, attempt=attempt,
                      phase=phase, outcome="failed", detail=reason, usage=reply.usage, duration_s=reply.duration_s),
                comment_on=[ticket],
                body=f"{self._header('builder', reply)} build attempt {attempt} failed: {reason}",
            )
            return None

        model = f"{reply.usage.provider}/{reply.usage.model}"
        message = (
            f"feat(#{ticket}): {issue['title']}\n\n{reply.text[:3000]}\n\n"
            f"Ticket: #{ticket}\nAgent: builder\nRuntime: OpenClaw\nModel: {model}\n"
            f"Attempt: {attempt}\nSession: {reply.session}\n" + (f"Note: {budget_note.strip()}\n" if budget_note else "")
        )
        workspace.git(repo, "-c", "user.name=builder (OpenClaw agent)", "-c", f"user.email={self.team.bot_email}",
                      "commit", "--quiet", "-m", message)
        push_branch(self.team, str(repo), branch, "builder")

        ignored = f"\n\nIgnored writes outside {self.team.allowed_paths}: {outside}" if outside else ""
        if pr is None or pr["state"] != "OPEN":
            pr_body = (
                f"Closes #{ticket}\n\n{self._header('builder', reply)} implementation summary:\n\n{reply.text[:6000]}"
                f"{ignored}\n\nGates: `agent/review` (reviewer, Hermes) and `agent/test` (tester, OpenClaw) "
                f"must pass on the same commit, then @{self.team.human_reviewer} reviews and merges."
            )
            pr_number = self.gh.create_pr("builder", branch, f"#{ticket}: {issue['title']}", pr_body)
        else:
            pr_number = pr["number"]
        self.gh.set_status_label("builder", ticket, "status:in-test")
        body = (
            f"{self._header('builder', reply)} **builder → tester**: attempt {attempt} pushed to `{branch}` "
            f"({len(changed)} files: {', '.join(f'`{c}`' for c in changed[:12])}). PR #{pr_number}.{ignored}{budget_note}"
        )
        self._record(
            Event(event="build", agent="builder", runtime="openclaw", ticket=ticket, pr=pr_number, attempt=attempt,
                  phase=phase, outcome="handoff:tester", usage=reply.usage, duration_s=reply.duration_s,
                  detail=json.dumps({"changed": changed, "outside": outside, "tools": reply.tools_used,
                                     "turns": reply.usage.api_calls, "max_turns": max_turns,
                                     "ended_early": not reply.ok})),
            comment_on=[ticket, pr_number], body=body,
        )
        return pr_number

    def _latest_assign_instructions(self, ticket: int) -> list[str]:
        for e in reversed(ledger.read()):
            if e.get("event") == "assign" and e.get("ticket") == ticket and e.get("detail"):
                try:
                    return json.loads(e["detail"]).get("instructions", [])
                except json.JSONDecodeError:
                    return []
        return []

    # -- review -----------------------------------------------------------------------------
    def review(self, ticket: int) -> str:
        issue = self.gh.get_issue("reviewer", ticket)
        pr = self.gh.find_pr("reviewer", self._branch(ticket), open_only=True)
        if pr is None:
            raise RuntimeError(f"no open team PR for ticket {ticket}")
        sha = pr["headRefOid"]
        attempt = self._attempts(ticket)
        # The diff is computed locally from the exact commit the status will be attached to, so a
        # push between "read diff" and "post status" cannot get an unreviewed commit approved.
        repo = workspace.harness_clone(self.team, "reviewer", "reviewer")
        workspace.checkout(repo, f"review/{ticket}", f"origin/{self._branch(ticket)}")
        if workspace.git(repo, "rev-parse", "HEAD").strip() != sha:
            raise RuntimeError(f"PR head moved while preparing review of #{ticket}; retry")
        diff = workspace.git(repo, "diff", f"origin/{self.team.base_branch}...{sha}")
        if len(diff) > MAX_DIFF_CHARS:
            diff = diff[:MAX_DIFF_CHARS] + "\n... [diff truncated by harness]"
        rc, output = run_tests_offline(repo, None)  # deterministic, no model call
        tests = f"pytest exit {rc}\n{output[-4000:]}"
        prompt = REVIEW_PROMPT.format(pr=pr["number"], ticket=ticket, title=issue["title"], body=issue["body"],
                                      constraints=self._constraints(), diff=diff, tests=tests)
        reply = self._hermes("reviewer", prompt, ticket=ticket)
        try:
            if not reply.ok:
                raise runtimes.AgentError(f"reviewer failed: {reply.error}")
            verdict = validate_review(runtimes.extract_json(reply.text))
        except runtimes.AgentError as exc:
            self._record(Event(event="review", agent="reviewer", runtime="hermes", ticket=ticket, pr=pr["number"],
                               attempt=attempt, phase=self._phase("review", attempt), outcome="failed",
                               usage=reply.usage, duration_s=reply.duration_s, detail=f"{exc}; reply: {reply.text[:300]}"))
            raise
        approved = verdict["verdict"] == "approve"
        findings = "\n".join(f"- `{f.get('file', '?')}`: {f.get('problem', '')} → {f.get('fix', '')}" for f in verdict["findings"])
        self.gh.set_commit_status("reviewer", sha, "agent/review", "success" if approved else "failure",
                                  f"reviewer: {verdict['summary']}")
        ready = approved and rc == 0
        nxt = f"@{self.team.human_reviewer} (human merge)" if ready else ("tester" if approved else "dispatch")
        body = (
            f"{self._header('reviewer', reply)} **reviewer → {nxt}**: "
            f"{'APPROVED' if approved else 'CHANGES REQUESTED'} on `{sha[:7]}` (attempt {attempt}).\n\n"
            f"{verdict['summary']}\n\n{findings}"
        )
        self.gh.set_status_label("reviewer", ticket, "status:ready-for-merge" if ready else
                                 ("status:in-test" if approved else "status:changes-requested"))
        self._record(
            Event(event="review", agent="reviewer", runtime="hermes", ticket=ticket, pr=pr["number"], attempt=attempt,
                  phase=self._phase("review", attempt), outcome=verdict["verdict"], usage=reply.usage,
                  duration_s=reply.duration_s, detail=verdict["summary"]),
            comment_on=[pr["number"]], body=body,
        )
        return verdict["verdict"]

    # -- test -------------------------------------------------------------------------------
    def test(self, ticket: int, focus: str | None = None, keep_existing: bool = False,
             model: str | None = None, max_turns: int | None = None) -> str:
        """Tester writes acceptance tests; the harness runs them offline and posts agent/test.

        focus/keep_existing support a targeted re-test: the tester's previous acceptance file is
        kept and the tester is asked to add only the tests described in `focus`.
        """
        issue = self.gh.get_issue("tester", ticket)
        pr = self.gh.find_pr("tester", self._branch(ticket), open_only=True)
        if pr is None:
            raise RuntimeError(f"no open team PR for ticket {ticket}")
        sha = pr["headRefOid"]
        attempt = self._attempts(ticket)
        repo = workspace.harness_clone(self.team, "tester", "tester")
        workspace.checkout(repo, f"test/{ticket}", f"origin/{self._branch(ticket)}")
        actual = workspace.git(repo, "rev-parse", "HEAD").strip()
        if actual != sha:
            raise RuntimeError(f"tester checkout {actual[:7]} does not match PR head {sha[:7]}")

        ws = config.WORK_DIR / "tester"
        workspace.export_tree(repo, ws / "repo")
        acc = ws / "acceptance"
        committed = repo / self._acceptance_path(ticket)
        seed = None
        if keep_existing:  # prefer the committed file (deterministic), else the last workspace copy
            seed = (committed.read_bytes() if committed.is_file() and not committed.is_symlink()
                    else workspace.read_agent_file(ws, acc / "test_acceptance.py"))
        if acc.is_symlink():
            acc.unlink()
        shutil.rmtree(acc, ignore_errors=True)
        acc.mkdir(parents=True)
        if seed is not None:
            workspace.safe_write(acc / "test_acceptance.py", seed.decode("utf-8", errors="replace"))
        workspace.safe_write(ws / "TICKET.md", render_ticket(issue, [], "", attempt))
        workspace.safe_write(ws / "BRIEF.md", config.BRIEF_FILE.read_text())

        message = TEST_MESSAGE.format(pr=pr["number"], ticket=ticket)
        if seed is not None and not focus:
            message += ("\n\nRE-RUN after a builder repair. acceptance/test_acceptance.py already contains "
                        "your tests. Read the updated code; change a test only if it is wrong about the "
                        "ticket. Do not delete or weaken tests to make them pass.")
        if focus:
            message += ("\n\nTARGETED RE-TEST requested by the human engineer. "
                        + ("acceptance/test_acceptance.py already contains your earlier tests: keep them and "
                           "append new tests to the same file; do not rewrite existing tests. " if seed else "")
                        + f"Add only these tests:\n{focus}\n"
                        "Write tests that check the behaviour the ticket and these points require. If the "
                        "implementation does not meet them, the tests must fail; do not weaken a test to make it pass.")
        session = f"ticket-{ticket}-test-{attempt}" + ("-recheck" if focus else "")
        reply = self._openclaw("tester", message, session=session, ticket=ticket, max_turns=max_turns, model=model)
        test_bytes = workspace.read_agent_file(ws, acc / "test_acceptance.py")
        # A budget-stopped run still counts if it produced a new test file. In a plain re-run
        # (kept tests, no focus) the tester may legitimately leave its tests unchanged.
        produced = test_bytes is not None and (seed is None or test_bytes != seed or not focus)
        if not produced:
            reason = reply.error or ("tester added no new tests" if seed else "tester wrote no acceptance/test_acceptance.py")
            self.gh.set_commit_status("tester", sha, "agent/test", "error", f"tester: {reason}"[:140])
            self._record(Event(event="test", agent="tester", runtime="openclaw", ticket=ticket, pr=pr["number"],
                               attempt=attempt, phase=self._phase("test", attempt), outcome="tester-error",
                               usage=reply.usage, duration_s=reply.duration_s, detail=reason),
                         comment_on=[pr["number"]],
                         body=f"{self._header('tester', reply)} tester could not produce tests: {reason}. Escalating.")
            self.escalate(ticket, "tester failed to produce acceptance tests")
            return "tester-error"

        test_source = test_bytes.decode("utf-8", errors="replace")
        written_in = f"{session} ({reply.usage.provider}/{reply.usage.model})"
        # Tester-owned updates to earlier tickets' acceptance files (modifications only).
        updates: dict[str, str] = {}
        own = self._acceptance_path(ticket)
        for p in sorted((repo / ACCEPTANCE_DIR).glob("test_ticket_*.py")):
            rel = p.relative_to(repo).as_posix()
            if rel == own or p.is_symlink():
                continue
            new = workspace.read_agent_file(ws, ws / "repo" / rel)
            if new is not None and new != p.read_bytes():
                updates[rel] = new.decode("utf-8", errors="replace")
        self.publish_tests(ticket, test_source, written_in, updates=updates)
        note = ((" Targeted re-test requested by the human engineer." if focus else "")
                + (f" Tester run ended early: {reply.error[:200]}." if not reply.ok else "")
                + f" Tester turns: {reply.usage.api_calls}" + (f" (budget ~{max_turns})" if max_turns else "") + ".")
        detail = {"focus": bool(focus), "kept_existing": seed is not None, "turns": reply.usage.api_calls,
                  "max_turns": max_turns, "ended_early": not reply.ok, "model_override": model}
        return self.verify(ticket, reply=reply, note=note, detail=detail)

    def _acceptance_path(self, ticket: int) -> str:
        return f"{ACCEPTANCE_DIR}test_ticket_{ticket}.py"

    def publish_tests(self, ticket: int, test_source: str, written_in: str,
                      updates: dict[str, str] | None = None) -> str:
        """Commit the tester's acceptance file to the ticket branch. Deterministic, no model call.

        Author: the tester agent (it wrote the content). Committer: the harness. Pushed with the
        builder's token because the tester's token is read-only for repository contents; the
        builder agent itself is not involved and cannot change this path.
        """
        branch = self._branch(ticket)
        repo = workspace.harness_clone(self.team, "builder", "builder")
        workspace.checkout(repo, branch, f"origin/{branch}")
        rel = self._acceptance_path(ticket)
        files = {rel: test_source, **{k: v for k, v in (updates or {}).items()
                                      if k.startswith(ACCEPTANCE_DIR) and k.endswith(".py")}}
        changed = []
        for path, content in files.items():
            target = repo / path
            if not workspace.contained(repo, target.parent / "x"):
                continue
            if target.is_file() and not target.is_symlink() and target.read_text() == content:
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            workspace.safe_write_bytes(target, content.encode())
            changed.append(path)
        if not changed:
            return workspace.git(repo, "rev-parse", "HEAD").strip()
        workspace.git(repo, "add", "--", *changed)
        message = (
            f"test(#{ticket}): publish tester acceptance tests\n\n"
            f"Acceptance tests written by the OpenClaw tester agent, copied verbatim into the\n"
            f"repository by the orchestrator. No model was called for this commit.\n\n"
            + (f"Also updates earlier acceptance tests (tester-owned): {', '.join(c for c in changed if c != rel)}\n\n"
               if any(c != rel for c in changed) else "")
            + f"Ticket: #{ticket}\nAgent: tester\nRuntime: OpenClaw\nWritten-in: {written_in}\n"
            f"Published-by: orchestrator harness (deterministic copy)\n"
            f"Pushed-with: builder token (tester token is read-only for contents)\n"
        )
        workspace.git(repo, "-c", f"user.name={PUBLISHER}", "-c", f"user.email={self.team.bot_email}",
                      "commit", "--quiet", f"--author=tester (OpenClaw agent) <{self.team.bot_email}>", "-m", message)
        push_branch(self.team, str(repo), branch, "builder")
        sha = workspace.git(repo, "rev-parse", "HEAD").strip()
        for _ in range(10):  # GitHub updates the PR head asynchronously
            pr = self.gh.find_pr("tester", branch, open_only=True)
            if pr and pr["headRefOid"] == sha:
                break
            time.sleep(2)
        ledger.append(Event(event="publish-tests", agent="tester", runtime="harness", ticket=ticket,
                            outcome="committed", detail=json.dumps({"sha": sha, "files": changed, "written_in": written_in})))
        return sha

    def verify(self, ticket: int, reply: runtimes.AgentReply | None = None, note: str = "",
               detail: dict | None = None) -> str:
        """Run the committed tests (builder's + tester's) on the PR head offline and post agent/test.

        Without `reply` this is a deterministic re-run: no model is called.
        """
        pr = self.gh.find_pr("tester", self._branch(ticket), open_only=True)
        if pr is None:
            raise RuntimeError(f"no open team PR for ticket {ticket}")
        sha = pr["headRefOid"]
        attempt = self._attempts(ticket)
        repo = workspace.harness_clone(self.team, "tester", "tester")
        workspace.checkout(repo, f"test/{ticket}", f"origin/{self._branch(ticket)}")
        if workspace.git(repo, "rev-parse", "HEAD").strip() != sha:
            raise RuntimeError(f"tester checkout does not match PR head {sha[:7]}")
        rel = self._acceptance_path(ticket)
        if not (repo / rel).is_file():
            raise RuntimeError(f"{rel} is not committed on {sha[:7]}; publish the tester's tests first")
        rc, output = run_tests_offline(repo, None)
        passed = rc == 0
        how = "committed tests" if reply is not None else "committed tests, deterministic re-run, no model call"
        self.gh.set_commit_status("tester", sha, "agent/test", "success" if passed else "failure",
                                  f"tester: pytest exit {rc} ({how})")
        nxt = "reviewer" if passed else "dispatch"
        header = self._header("tester", reply) if reply is not None else "**[tester · openclaw · harness re-run, no model call]**"
        body = (
            f"{header} **tester → {nxt}**: {'PASS' if passed else 'FAIL'} on `{sha[:7]}` (pytest exit {rc}, offline "
            f"container, {how}: builder tests + tester acceptance tests in `{rel}`).{(' ' + note) if note else ''}\n\n"
            f"<details><summary>pytest output (tail)</summary>\n\n```\n{output[-6000:]}\n```\n</details>"
        )
        self.gh.set_status_label("tester", ticket, "status:in-review" if passed else "status:changes-requested")
        self._record(
            Event(event="test", agent="tester", runtime="openclaw" if reply is not None else "harness", ticket=ticket,
                  pr=pr["number"], attempt=attempt, phase=self._phase("test", attempt),
                  outcome="pass" if passed else "fail", usage=reply.usage if reply is not None else None,
                  duration_s=reply.duration_s if reply is not None else None,
                  detail=json.dumps({"pytest_exit": rc, "sha": sha, "model_call": reply is not None, **(detail or {})})),
            comment_on=[pr["number"]], body=body,
        )
        return "pass" if passed else "fail"

    # -- escalation / driver ----------------------------------------------------------------
    def escalate(self, ticket: int, reason: str) -> None:
        self.gh.set_status_label("dispatch", ticket, "status:needs-human")
        self._record(Event(event="escalate", agent="dispatch", runtime="hermes", ticket=ticket,
                           outcome="needs-human", detail=reason),
                     comment_on=[ticket],
                     body=f"{self._header('dispatch')} **dispatch → @{self.team.human_reviewer}**: escalated. {reason}")
        return None

    def step(self) -> str:
        """Advance the board by exactly one agent action. Returns a short description."""
        tickets = self.gh.list_tickets("dispatch")
        if not tickets:
            created = self.plan()
            return f"planned tickets {created}"
        open_tickets = [t for t in tickets if t["state"] == "OPEN"]
        for status, action in (("status:in-test", self.test), ("status:in-review", self.review),
                               ("status:assigned", self.build)):
            t = next((t for t in open_tickets if self._status(t) == status), None)
            if t:
                result = action(t["number"])
                return f"{action.__name__} #{t['number']} -> {result}"
        assigned = self.assign()
        if assigned is not None:
            return f"assign -> #{assigned}"
        waiting = [t["number"] for t in open_tickets if self._status(t) == "status:ready-for-merge"]
        return f"idle (waiting for human merge of {waiting})" if waiting else "idle (nothing assignable)"


def validate_plan(data: dict) -> list[dict]:
    tickets = data.get("tickets")
    if not isinstance(tickets, list) or not tickets:
        raise runtimes.AgentError("plan has no tickets")
    seen = set()
    out = []
    for t in tickets:
        key = str(t.get("key") or f"T{len(out) + 1}")
        title = str(t.get("title") or "").strip()
        criteria = [str(c) for c in t.get("acceptance_criteria") or [] if str(c).strip()]
        if not title or not criteria:
            raise runtimes.AgentError(f"ticket {key} lacks a title or acceptance criteria")
        out.append({
            "key": key, "title": title[:120], "description": str(t.get("description") or ""),
            "acceptance_criteria": criteria,
            "depends_on": [str(d) for d in t.get("depends_on") or [] if str(d) in seen],
            "files": [str(f) for f in t.get("files") or []],
        })
        seen.add(key)
    return out


def validate_review(data: dict) -> dict:
    verdict = str(data.get("verdict", "")).strip().lower()
    if verdict not in ("approve", "request_changes"):
        raise runtimes.AgentError(f"reviewer verdict must be approve|request_changes, got {verdict!r}")
    findings = data.get("findings") or []
    if verdict == "request_changes" and not findings:
        raise runtimes.AgentError("reviewer requested changes without findings")
    return {"verdict": verdict, "summary": str(data.get("summary") or "")[:500],
            "findings": [f for f in findings if isinstance(f, dict)]}


def render_ticket(issue: dict, instructions: list[str], feedback: str, attempt: int) -> str:
    parts = [f"# Ticket #{issue['number']}: {issue['title']}", f"Attempt: {attempt}", "", issue["body"]]
    if instructions:
        parts += ["", "## Instructions from dispatch",
                  "(If an instruction conflicts with the acceptance criteria above, the acceptance criteria win.)",
                  *[f"- {i}" for i in instructions]]
    if feedback:
        parts += ["", "## Feedback from the previous attempt (fix every point)", feedback]
    return "\n".join(parts) + "\n"


def run_tests_offline(repo: Path, acceptance_source: str | None = None) -> tuple[int, str]:
    """Run builder tests + tester acceptance tests in a locked-down container (no network, no secrets)."""
    with tempfile.TemporaryDirectory(prefix="newsroom-test-") as tmp:
        src = Path(tmp) / "work"
        workspace.copy_tree_no_links(repo, src)
        acc = Path(tmp) / "acceptance"
        acc.mkdir()
        targets = ["tests"] if (src / "app" / "tests").is_dir() else []
        if acceptance_source is not None:
            (acc / "test_acceptance.py").write_text(acceptance_source)
            targets.insert(0, "/acceptance")
        name = f"newsroom-test-{uuid.uuid4().hex[:10]}"
        cmd = [
            "docker", "run", "--rm", "--name", name, "--network", "none", "--read-only", "--tmpfs", "/tmp:rw,size=64m",
            "--cap-drop", "ALL", "--security-opt", "no-new-privileges", "--pids-limit", "256",
            "--memory", "1g", "--cpus", "1", "--user", "1000:1000",
            "-v", f"{src}:/work:ro", "-v", f"{acc}:/acceptance:ro", "-w", "/work/app",
            "-e", "PYTHONPATH=/work/app", "-e", "NEWSROOM_DB=/tmp/newsroom-test.db",
            config.TEST_IMAGE, "python", "-m", "pytest", "-q", "-rfE", "--tb=short", "-W", "ignore::DeprecationWarning",
            "-p", "no:cacheprovider", "--rootdir", "/work/app",
            *targets,
        ]
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=TEST_TIMEOUT_S, env=config.base_env())
        except subprocess.TimeoutExpired:
            subprocess.run(["docker", "kill", name], capture_output=True, env=config.base_env())
            return 124, f"tests exceeded {TEST_TIMEOUT_S}s"
        return proc.returncode, (proc.stdout + proc.stderr)
