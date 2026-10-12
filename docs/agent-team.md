# Agent delivery team

How the Hermes + OpenClaw team takes a ticket from idea to merged code. The roster is
configured in [`agents/team.json`](../agents/team.json); this page explains the design.

## Roster

| Agent | Runtime | Role | Primary model → fallbacks | Tools | GitHub token |
|---|---|---|---|---|---|
| `dispatch` | Hermes profile `dispatch` | Orchestrator: brief → tickets, assigns, re-assigns rejected work | `ollama-cloud/nemotron-3-ultra` → `ollama-cloud/gpt-oss:120b` → `openrouter/nemotron-3-ultra:free` → local `llama3.1:8b` | `todo` only (no shell, files or web) | `dispatch`: issues r/w |
| `builder` | OpenClaw agent `builder` | Implements one ticket per PR, only under `app/` | `ollama-cloud/gpt-oss:120b` → `ollama-cloud/nemotron-3-super` → `openrouter/laguna-s-2.1:free` → local `qwen2.5:7b` | OpenClaw `coding` profile; exec denied; Docker sandbox, no network | `builder`: contents + PRs r/w, **no commit statuses** |
| `reviewer` | Hermes profile `reviewer` | Reads each PR diff against its ticket; approves or sends back | `ollama-cloud/nemotron-3-super` → `ollama-cloud/gpt-oss:120b` → `openrouter/gemma-4-31b:free` → local `llama3.1:8b` | `todo` only | `reviewer`: PRs + statuses |
| `tester` | OpenClaw agent `tester` | Writes independent acceptance tests from the ticket; harness runs them offline | `ollama-cloud/gpt-oss:20b` → `ollama-cloud/nemotron-3-nano:30b` → `openrouter/nemotron-3-super:free` → local `qwen2.5:7b` | same sandbox as builder | `tester`: PRs + statuses |
| human (`Ankita2525`) | — | Reviews and merges | — | — | ruleset approval |

Model choice is constrained by the Ollama Cloud free plan. Probed on 2026-10-11 with 1-token
requests: `nemotron-3-ultra`, `nemotron-3-super`, `nemotron-3-nano:30b`, `gpt-oss:120b`,
`gpt-oss:20b` and `gemma4:31b` are included; `kimi-*`, `glm-*`, `minimax-*`, `deepseek-*` and
`mistral-large-*` return HTTP 402 ("not included in your free usage").

Why this split:

- **Planning on the strongest model, routine work on cheaper ones.** Dispatch makes few,
  high-leverage calls; the builder makes the most calls, so it gets a coding model; the
  tester's job (write tests from criteria) is narrow, so it gets the cheapest model.
- **Runtimes by trust level.** Agents that touch code written by a model (builder, tester)
  run on OpenClaw, inside its Docker sandbox with no network and no exec. Agents that only
  read and judge (dispatch, reviewer) run on Hermes with no tools beyond a todo list.
- **Separate tester from reviewer.** The reviewer reads, the tester runs. A summarizer
  that "looks right" but crashes on import gets caught by the tester even if the reviewer
  misses it.
- **One ticket in flight at a time.** The Ollama Cloud free plan allows one concurrent
  request, so parallel builders would only queue.

## Ticket flow

```mermaid
flowchart LR
    brief[agents/shared/brief.md] -->|plan| D1[dispatch<br/>Hermes]
    D1 -->|creates issues<br/>status:todo| I[(GitHub Issues)]
    I -->|assign: deps merged| D2[dispatch]
    D2 -->|status:assigned<br/>comment dispatch→builder| B[builder<br/>OpenClaw sandbox]
    B -->|commit as builder, push ticket/N,<br/>open PR, status:in-test| T[tester<br/>OpenClaw sandbox]
    T -->|writes acceptance tests;<br/>harness commits them to the PR| X[offline pytest on<br/>the new PR head]
    X -->|agent/test=failure<br/>status:changes-requested| D2
    X -->|agent/test=success<br/>status:in-review| R[reviewer<br/>Hermes]
    R -->|agent/review=failure<br/>status:changes-requested| D2
    R -->|agent/review=success on the same commit<br/>status:ready-for-merge| H[human merges PR]
    H -->|Closes #N| I
    D2 -->|3 failed attempts| E[status:needs-human]
```

Every arrow is a GitHub label change plus a comment headed `**[agent · runtime · model]**`,
carrying a hidden `<!-- newsroom-event {...} -->` marker with the attempt number, outcome,
model and token count. The same event goes to `~/newsroom-work/ledger.jsonl`, which feeds
`python3 -m orchestrator report`.

## What blocks bad work

| Gate | Enforced by |
|---|---|
| `main` only changes through PRs with `agent/review` + `agent/test` success and a human approval | GitHub ruleset `main-agent-quality-gates` (verified in `docs/traces/permission-test.md`) |
| Builder cannot mark its own work reviewed/tested | Builder token has no commit-status permission (HTTP 403, verified) |
| Rejected work goes back with the findings | Reviewer/tester set `status:changes-requested`; dispatch's next assignment quotes the findings; builder's `TICKET.md` includes them |
| Retry limit | After `max_attempts` (3) builds, dispatch escalates with `status:needs-human` |
| Builder can only change `app/` | Harness imports only regular files under `app/` from the agent's copy; other writes are reported in the handoff comment and dropped |
| Builder cannot touch the tester's tests | Files under `app/tests/acceptance/` are never changed or deleted by a builder import; attempts are reported |
| Tests are reproducible from the repo | The tester's acceptance file is committed to the PR (`app/tests/acceptance/test_ticket_<N>.py`) before the final test run and review |
| Dispatch can only assign tickets whose dependencies are merged | Harness validates the choice; an invalid choice is logged and re-asked once, never silently replaced |
| Tests run the code, not just read it | Builder tests + tester's acceptance tests run in `newsroom-test-runner` with `--network none`, read-only root, `cap-drop ALL`, no tokens |

## What the harness does and does not do

`orchestrator/` is plumbing written by the human engineer (with AI assistance). It creates
git checkouts, passes each agent's prompt to its runtime, commits the builder's files
**under the builder's identity**, calls the GitHub API **with the acting agent's own token**,
runs tests in the container, and writes the ledger. It never writes product code, never
chooses tickets, and never decides review or test outcomes:

- the ticket list and every assignment come from `dispatch`'s reply;
- every file under `app/` comes from `builder`'s or `tester`'s tool calls;
- every `agent/review` status comes from `reviewer`'s JSON verdict;
- every `agent/test` status is the exit code of pytest on the tester's and builder's tests as
  committed on that exact PR head (`verify` re-runs them deterministically, without a model).

The harness performs commits for the builder because the builder's sandbox has exec
denied and no network (deliberately).

**Credential boundary for test publication.** The tester's token is read-only for repository
contents, by design: a gatekeeper that could push code could change what it judges. To commit
the tester's acceptance file, the harness pushes with the *builder's* token. That commit is
authored `tester (OpenClaw agent)`, committed by `newsroom-orchestrator (harness)`, says in its
message that no model was called, and touches only `app/tests/acceptance/test_ticket_<N>.py`.
The builder agent is not involved, and builder imports cannot modify that directory. All
tokens belong to the same machine account, so GitHub itself cannot distinguish them. Each commit records the agent, runtime, model,
attempt and OpenClaw session id in trailers, so it can be matched to the OpenClaw session log.

## Security notes

The repository is public, so anyone can open issues, comment, and open fork PRs. The harness
treats all of that as untrusted:

- **Only team artifacts drive the workflow.** Tickets must be authored by the machine account;
  PRs must come from the same repository (no forks), be authored by the machine account and
  target `main`; review/test feedback that is fed back to the builder comes only from the
  machine account's comments.
- **Event markers cannot be forged.** Only a `newsroom-event` marker at the very end of a
  machine-account comment counts; the harness appends it after all agent-written text.
- **Statuses land on the reviewed commit.** The reviewer's diff is computed locally from the
  exact PR head SHA that the `agent/review` status is posted to; if the head moved, the review
  is aborted. The tester verifies its checkout matches the PR head the same way.
- **Agents never see a `.git` directory.** They work on a plain copy of the tree, so a
  prompt-injected agent cannot plant git hooks or `core.fsmonitor` settings that would run
  on the host. Symlinks are dropped when exporting trees to agents or to the test container,
  never followed; agent files are read with `O_NOFOLLOW` and must resolve inside the
  workspace; harness files written into agent workspaces (`TICKET.md`, `BRIEF.md`,
  `AGENTS.md`) replace rather than follow a planted symlink.
- **Environments are allowlists.** Agent runtimes get `HOME`, `PATH`, locale and the model
  keys named in `MODEL_KEY_NAMES`, nothing else from the operator's shell. `gh`/`git` get one
  agent's token plus an empty `GH_CONFIG_DIR` (no fallback to the human's login) and ignore
  global/system git config (no `insteadOf` redirection of the token). Tokens are passed via
  environment, never argv; token files must be owned by the user with mode 600.
- **Hermes agents have no tools that touch the system.** `-t todo` was verified from a live
  session export: the only tool call available was `todo_list`. (An empty `-t` would load
  Hermes's default tool set, including the terminal; the harness refuses it.)
- **OpenClaw exec is denied at runtime**, verified live: the builder's `exec` call returned
  `exec denied: host=sandbox security=deny`. The `coding` tool profile only exposes the tool
  schema.
- `scripts/setup_agents.py` refuses to run if the OpenClaw profile's verified security
  baseline (sandbox all, network none, read-only root, cap-drop ALL, exec deny, elevated off,
  loopback token-auth gateway) has drifted.

## Running it

```bash
# one-time
python3 scripts/setup_agents.py          # configure Hermes profiles, OpenClaw agents, test image
scripts/start-gateway.sh                 # in its own terminal: gateway with model keys

# drive the board
python3 -m orchestrator plan             # dispatch creates the tickets
python3 -m orchestrator step             # one agent action (assign/build/review/test)
python3 -m orchestrator run --max 10     # loop until idle or waiting for a human merge
python3 -m orchestrator report           # token & model report

# harness tests (no network, no tokens needed)
uv run --python 3.14 --with pytest python -m pytest -q orchestrator/tests
```

## Known limitations

- All four agents act through one GitHub machine account (`newsroom-ak-bot`), so GitHub
  shows one user; per-agent identity is in commit author names, trailers, comment headers and
  per-agent tokens (see `docs/agent-identity.md`).
- Reviewer and tester tokens can both write any status context; separation between them
  is by convention in the harness, not enforced by GitHub.
- A PR diff can contain prompt-injection text aimed at the reviewer. The reviewer has no
  tools, so the worst case is a wrong verdict; the tester still runs the code and a human
  must approve the merge.
- Local fallback models are weak at tool use and planning. The local Ollama server truncates
  prompts at its default context unless started with `OLLAMA_CONTEXT_LENGTH=65536`; Hermes
  requires a 64K window. Local models are a last resort, not a working tier.
