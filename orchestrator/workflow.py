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
import uuid
from pathlib import Path

from . import config, ledger, runtimes, workspace
from .gh import GitHub, push_branch
from .ledger import Event

DEPENDS_RE = re.compile(r"^Depends on:\s*(.*)$", re.MULTILINE | re.IGNORECASE)
MAX_DIFF_CHARS = 60_000
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

Feedback on candidates that were sent back by review or test:
{feedback}

Return ONLY a JSON object:
{{"ticket": <issue number from the candidates>, "reason": "<one sentence>",
  "instructions": ["<what the builder must do or fix, at most 5 bullet points>"]}}
"""

REVIEW_PROMPT = """\
Review pull request #{pr} against its ticket.

TICKET #{ticket}: {title}
{body}

ENGINEERING CONSTRAINTS (from the brief):
{constraints}

DIFF:
{diff}

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
"""


class Orchestrator:
    def __init__(self, team: config.Team | None = None, github: GitHub | None = None):
        self.team = team or config.load_team()
        self.gh = github or GitHub(self.team)

    # -- helpers ----------------------------------------------------------------------------
    def _hermes(self, agent: str, prompt: str) -> runtimes.AgentReply:
        spec = self.team.agent(agent)
        return runtimes.run_hermes(spec["profile"], prompt, toolsets=spec.get("toolsets", "todo"))

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
        prompt = ASSIGN_PROMPT.format(board=board, feedback=feedback or "none")
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
                return c["body"][:4000]
        return ""

    # -- build ------------------------------------------------------------------------------
    def build(self, ticket: int) -> int | None:
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

        reply = runtimes.run_openclaw(
            self.team.agent("builder")["agent_id"],
            BUILD_MESSAGE.format(ticket=ticket, attempt=attempt, max_attempts=self.team.max_attempts),
            session=f"ticket-{ticket}-build-{attempt}",
        )
        changed, outside = workspace.import_changes(ws / "repo", repo, self.team.allowed_paths)
        phase = self._phase("coding", attempt)
        if not reply.ok or not changed:
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
            f"Attempt: {attempt}\nSession: {reply.session}\n"
        )
        workspace.git(repo, "-c", "user.name=builder (OpenClaw agent)", "-c", f"user.email={self.team.bot_email}",
                      "commit", "--quiet", "-m", message)
        push_branch(self.team, str(repo), branch, "builder")

        ignored = f"\n\nIgnored writes outside {self.team.allowed_paths}: {outside}" if outside else ""
        if pr is None or pr["state"] != "OPEN":
            pr_body = (
                f"Closes #{ticket}\n\n{self._header('builder', reply)} implementation summary:\n\n{reply.text[:6000]}"
                f"{ignored}\n\nGates: `agent/review` (reviewer, Hermes) and `agent/test` (tester, OpenClaw) "
                f"must pass, then @{self.team.human_reviewer} reviews and merges."
            )
            pr_number = self.gh.create_pr("builder", branch, f"#{ticket}: {issue['title']}", pr_body)
        else:
            pr_number = pr["number"]
        self.gh.set_status_label("builder", ticket, "status:in-review")
        body = (
            f"{self._header('builder', reply)} **builder → reviewer**: attempt {attempt} pushed to `{branch}` "
            f"({len(changed)} files: {', '.join(f'`{c}`' for c in changed[:12])}). PR #{pr_number}.{ignored}"
        )
        self._record(
            Event(event="build", agent="builder", runtime="openclaw", ticket=ticket, pr=pr_number, attempt=attempt,
                  phase=phase, outcome="handoff:reviewer", usage=reply.usage, duration_s=reply.duration_s,
                  detail=json.dumps({"changed": changed, "outside": outside, "tools": reply.tools_used})),
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
        prompt = REVIEW_PROMPT.format(pr=pr["number"], ticket=ticket, title=issue["title"], body=issue["body"],
                                      constraints=self._constraints(), diff=diff)
        reply = self._hermes("reviewer", prompt)
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
        nxt = "tester" if approved else "dispatch"
        body = (
            f"{self._header('reviewer', reply)} **reviewer → {nxt}**: "
            f"{'APPROVED' if approved else 'CHANGES REQUESTED'} on `{sha[:7]}` (attempt {attempt}).\n\n"
            f"{verdict['summary']}\n\n{findings}"
        )
        self.gh.set_status_label("reviewer", ticket, "status:in-test" if approved else "status:changes-requested")
        self._record(
            Event(event="review", agent="reviewer", runtime="hermes", ticket=ticket, pr=pr["number"], attempt=attempt,
                  phase=self._phase("review", attempt), outcome=verdict["verdict"], usage=reply.usage,
                  duration_s=reply.duration_s, detail=verdict["summary"]),
            comment_on=[pr["number"]], body=body,
        )
        return verdict["verdict"]

    # -- test -------------------------------------------------------------------------------
    def test(self, ticket: int) -> str:
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
        if acc.is_symlink():
            acc.unlink()
        shutil.rmtree(acc, ignore_errors=True)
        acc.mkdir(parents=True)
        workspace.safe_write(ws / "TICKET.md", render_ticket(issue, [], "", attempt))
        workspace.safe_write(ws / "BRIEF.md", config.BRIEF_FILE.read_text())

        reply = runtimes.run_openclaw(self.team.agent("tester")["agent_id"],
                                      TEST_MESSAGE.format(pr=pr["number"], ticket=ticket),
                                      session=f"ticket-{ticket}-test-{attempt}")
        test_bytes = workspace.read_agent_file(ws, acc / "test_acceptance.py")
        if not reply.ok or test_bytes is None:
            reason = reply.error or "tester wrote no acceptance/test_acceptance.py"
            self.gh.set_commit_status("tester", sha, "agent/test", "error", f"tester: {reason}"[:140])
            self._record(Event(event="test", agent="tester", runtime="openclaw", ticket=ticket, pr=pr["number"],
                               attempt=attempt, phase=self._phase("test", attempt), outcome="tester-error",
                               usage=reply.usage, duration_s=reply.duration_s, detail=reason),
                         comment_on=[pr["number"]],
                         body=f"{self._header('tester', reply)} tester could not produce tests: {reason}. Escalating.")
            self.escalate(ticket, "tester failed to produce acceptance tests")
            return "tester-error"

        test_source = test_bytes.decode("utf-8", errors="replace")
        rc, output = run_tests_offline(repo, test_source)
        passed = rc == 0
        self.gh.set_commit_status("tester", sha, "agent/test", "success" if passed else "failure",
                                  f"tester: pytest exit {rc}")
        nxt = f"@{self.team.human_reviewer} (human merge)" if passed else "dispatch"
        body = (
            f"{self._header('tester', reply)} **tester → {nxt}**: {'PASS' if passed else 'FAIL'} on `{sha[:7]}` "
            f"(pytest exit {rc}, offline container, builder tests + independent acceptance tests).\n\n"
            f"<details><summary>pytest output (tail)</summary>\n\n```\n{output[-6000:]}\n```\n</details>\n\n"
            f"<details><summary>acceptance/test_acceptance.py (written by tester)</summary>\n\n```python\n"
            f"{test_source[:12000]}\n```\n</details>"
        )
        self.gh.set_status_label("tester", ticket, "status:ready-for-merge" if passed else "status:changes-requested")
        self._record(
            Event(event="test", agent="tester", runtime="openclaw", ticket=ticket, pr=pr["number"], attempt=attempt,
                  phase=self._phase("test", attempt), outcome="pass" if passed else "fail", usage=reply.usage,
                  duration_s=reply.duration_s, detail=f"pytest exit {rc}"),
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
        parts += ["", "## Instructions from dispatch", *[f"- {i}" for i in instructions]]
    if feedback:
        parts += ["", "## Feedback from the previous attempt (fix every point)", feedback]
    return "\n".join(parts) + "\n"


def run_tests_offline(repo: Path, acceptance_source: str) -> tuple[int, str]:
    """Run builder tests + tester acceptance tests in a locked-down container (no network, no secrets)."""
    with tempfile.TemporaryDirectory(prefix="newsroom-test-") as tmp:
        src = Path(tmp) / "work"
        workspace.copy_tree_no_links(repo, src)
        acc = Path(tmp) / "acceptance"
        acc.mkdir()
        (acc / "test_acceptance.py").write_text(acceptance_source)
        targets = ["/acceptance"] + (["tests"] if (src / "app" / "tests").is_dir() else [])
        name = f"newsroom-test-{uuid.uuid4().hex[:10]}"
        cmd = [
            "docker", "run", "--rm", "--name", name, "--network", "none", "--read-only", "--tmpfs", "/tmp:rw,size=64m",
            "--cap-drop", "ALL", "--security-opt", "no-new-privileges", "--pids-limit", "256",
            "--memory", "1g", "--cpus", "1", "--user", "1000:1000",
            "-v", f"{src}:/work:ro", "-v", f"{acc}:/acceptance:ro", "-w", "/work/app",
            "-e", "PYTHONPATH=/work/app", "-e", "NEWSROOM_DB=/tmp/newsroom-test.db",
            config.TEST_IMAGE, "python", "-m", "pytest", "-q", "-p", "no:cacheprovider", "--rootdir", "/work/app",
            *targets,
        ]
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=TEST_TIMEOUT_S, env=config.base_env())
        except subprocess.TimeoutExpired:
            subprocess.run(["docker", "kill", name], capture_output=True, env=config.base_env())
            return 124, f"tests exceeded {TEST_TIMEOUT_S}s"
        return proc.returncode, (proc.stdout + proc.stderr)
