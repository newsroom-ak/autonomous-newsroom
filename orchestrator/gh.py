"""GitHub operations, each executed with exactly one agent's token.

Tokens go to `gh` through the GH_TOKEN environment variable and to `git push` through a
credential helper that reads an environment variable, so they never appear in argv or logs.
"""

from __future__ import annotations

import json
import os
import re
import subprocess

from . import config

STATUS_LABELS = (
    "status:todo",
    "status:assigned",
    "status:in-review",
    "status:in-test",
    "status:changes-requested",
    "status:ready-for-merge",
    "status:needs-human",
)
EVENT_MARKER = re.compile(r"<!-- newsroom-event (\{.*?\}) -->")


class GitHub:
    def __init__(self, team: config.Team):
        self.team = team
        self.repo = team.repo

    # -- plumbing -------------------------------------------------------------------------
    def _env(self, agent: str) -> dict[str, str]:
        env = {k: v for k, v in os.environ.items() if not k.startswith(config.SECRET_ENV_PREFIXES)}
        env["GH_TOKEN"] = config.github_token(agent, self.team)
        return env

    def gh(self, agent: str, *args: str, input_text: str | None = None) -> str:
        """Run gh with `agent`'s token. The human's own gh login is never used."""
        proc = subprocess.run(
            ["gh", *args], env=self._env(agent), input=input_text, capture_output=True, text=True
        )
        if proc.returncode != 0:
            raise RuntimeError(f"gh {args[0]} {args[1] if len(args) > 1 else ''} failed as {agent}: {proc.stderr.strip()}")
        return proc.stdout

    def api(self, agent: str, path: str, method: str = "GET", fields: dict | None = None) -> object:
        args = ["api", "-X", method, path]
        body = None
        if fields is not None:
            args += ["--input", "-"]
            body = json.dumps(fields)
        out = self.gh(agent, *args, input_text=body)
        return json.loads(out) if out.strip() else None

    # -- labels / issues ------------------------------------------------------------------
    def ensure_labels(self, agent: str) -> None:
        existing = {l["name"] for l in json.loads(self.gh(agent, "label", "list", "-R", self.repo, "--json", "name", "--limit", "200"))}
        for name in ("ticket", *STATUS_LABELS):
            if name not in existing:
                self.gh(agent, "label", "create", name, "-R", self.repo, "--color", "5319e7" if name == "ticket" else "c5def5")

    def create_issue(self, agent: str, title: str, body: str, labels: list[str]) -> int:
        url = self.gh(agent, "issue", "create", "-R", self.repo, "--title", title, "--body-file", "-",
                      *sum((["--label", l] for l in labels), []), input_text=body).strip()
        return int(url.rstrip("/").rsplit("/", 1)[1])

    def list_tickets(self, agent: str, state: str = "all") -> list[dict]:
        out = self.gh(agent, "issue", "list", "-R", self.repo, "--label", "ticket", "--state", state,
                      "--limit", "100", "--json", "number,title,body,state,labels")
        tickets = json.loads(out)
        for t in tickets:
            t["labels"] = [l["name"] for l in t["labels"]]
        return sorted(tickets, key=lambda t: t["number"])

    def get_issue(self, agent: str, number: int) -> dict:
        out = self.gh(agent, "issue", "view", str(number), "-R", self.repo,
                      "--json", "number,title,body,state,labels,comments")
        issue = json.loads(out)
        issue["labels"] = [l["name"] for l in issue["labels"]]
        return issue

    def set_status_label(self, agent: str, number: int, status: str) -> None:
        current = [l for l in self.get_issue(agent, number)["labels"] if l.startswith("status:")]
        args = ["issue", "edit", str(number), "-R", self.repo, "--add-label", status]
        for l in current:
            if l != status:
                args += ["--remove-label", l]
        self.gh(agent, *args)

    def comment(self, agent: str, number: int, body: str, event: dict | None = None) -> None:
        if event is not None:
            body += f"\n\n<!-- newsroom-event {json.dumps(event, sort_keys=True)} -->"
        self.gh(agent, "issue", "comment", str(number), "-R", self.repo, "--body-file", "-", input_text=body)

    def events(self, agent: str, number: int) -> list[dict]:
        """Machine-readable events previously posted on an issue or PR conversation."""
        found = []
        for c in self.get_issue(agent, number).get("comments", []):
            for m in EVENT_MARKER.finditer(c.get("body", "")):
                found.append(json.loads(m.group(1)))
        return found

    # -- pull requests -----------------------------------------------------------------------
    def find_pr(self, agent: str, branch: str) -> dict | None:
        out = self.gh(agent, "pr", "list", "-R", self.repo, "--head", branch, "--state", "all",
                      "--json", "number,state,headRefOid,url,mergedAt")
        prs = json.loads(out)
        return prs[0] if prs else None

    def create_pr(self, agent: str, branch: str, title: str, body: str) -> int:
        url = self.gh(agent, "pr", "create", "-R", self.repo, "--base", self.team.base_branch,
                      "--head", branch, "--title", title, "--body-file", "-", input_text=body).strip()
        return int(url.rstrip("/").rsplit("/", 1)[1])

    def pr_diff(self, agent: str, number: int) -> str:
        return self.gh(agent, "pr", "diff", str(number), "-R", self.repo)

    def pr_head(self, agent: str, number: int) -> str:
        return json.loads(self.gh(agent, "pr", "view", str(number), "-R", self.repo, "--json", "headRefOid"))["headRefOid"]

    def set_commit_status(self, agent: str, sha: str, context: str, state: str, description: str, target_url: str | None = None) -> None:
        fields = {"state": state, "context": context, "description": description[:140]}
        if target_url:
            fields["target_url"] = target_url
        self.api(agent, f"repos/{self.repo}/statuses/{sha}", method="POST", fields=fields)


def git_as(team: config.Team, agent: str, worktree: str, *args: str) -> str:
    """Run a networked git command (fetch/push) with `agent`'s token, kept out of argv."""
    token = config.github_token(agent, team)
    env = {k: v for k, v in os.environ.items() if not k.startswith(config.SECRET_ENV_PREFIXES)}
    env["NEWSROOM_PUSH_TOKEN"] = token
    env["GIT_TERMINAL_PROMPT"] = "0"
    helper = '!f() { echo username=x-access-token; echo "password=$NEWSROOM_PUSH_TOKEN"; }; f'
    proc = subprocess.run(
        ["git", "-C", worktree, "-c", "credential.helper=", "-c", f"credential.helper={helper}", *args],
        env=env, capture_output=True, text=True,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"git {args[0]} as {agent} failed: {proc.stderr.strip()[-500:]}")
    return proc.stdout


def push_branch(team: config.Team, worktree: str, branch: str, agent: str) -> None:
    git_as(team, agent, worktree, "push", f"https://github.com/{team.repo}.git", f"HEAD:refs/heads/{branch}")
