# Agent identity and merge gates

Status: **design**. Each control below is marked as planned until a live test proves it;
results will be recorded in `docs/traces/`.

## Accounts

- **Human:** `Ankita2525`, organization owner of `newsroom-ak`. The only account that approves and merges.
- **Machine account:** one account, as GitHub's Terms of Service allow one free machine account
  per person. All four agents act through it, each with its own fine-grained token limited to
  this repository.

## Token permissions (planned)

| Agent | Runtime | Contents | Pull requests | Issues | Commit statuses | Workflows |
|---|---|---|---|---|---|---|
| dispatch | Hermes | read | read | read/write | none | none |
| builder | OpenClaw | read/write | read/write | read/write | **none** | **none** |
| reviewer | Hermes | read | read/write | read/write | read/write | none |
| tester | OpenClaw | read | read/write | read/write | read/write | none |

GitHub's REST docs state that creating a commit status requires the fine-grained
"Commit statuses" permission (write). The builder token omits it, so the builder cannot
mark `agent/review` or `agent/test` as successful. **Live verification: pending.**

## Merge gate on `main` (planned)

- Changes only through pull requests.
- Required statuses on the latest commit: `agent/review` and `agent/test`.
- One approving review required; only the human account can provide it.
- No bypass actors. The machine account has Write role, not Admin, so it cannot edit the ruleset.
- GitHub Actions disabled, so no workflow can post statuses on an agent's behalf.

## Known limitations

1. All agents share one GitHub identity. Agent attribution relies on per-agent git author names
   and and agent-identifying comment headers, which GitHub does not enforce.
2. GitHub can restrict a required status to a specific source only for GitHub Apps, not for
   tokens. Any token with Commit statuses write satisfies the check.
3. Reviewer and tester share the same status permission; each could technically post the
   other's context. Only builder-vs-gatekeeper separation is enforced by GitHub.
4. An account cannot formally review its own PR, so rejections appear as a failed
   `agent/review` status plus a comment rather than a GitHub "Request changes" review.
5. Per-agent credentials on one host are only as isolated as the host setup. OpenClaw's docs
   state its per-agent GitHub identities are not an OS-level sandbox. Mitigation (planned):
   builder runs in OpenClaw's per-agent sandbox; Hermes runs as a separate Linux user.
6. The tester executes builder-written code. Mitigation (planned): tests run in a container
   with no GitHub token present.
7. The human holds the machine account's password and 2FA. The ruleset has no bypass, including
   for the human.
