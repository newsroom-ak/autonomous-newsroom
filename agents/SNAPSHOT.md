# Agent configuration snapshots

The brief asks for "each agent's OpenClaw or Hermes config, prompts and skills (secrets removed)". This is
where each lives.

| Agent | Runtime | Prompt (identity and rules) | Configuration snapshot | Per-run settings |
|---|---|---|---|---|
| dispatch | Hermes profile `dispatch` | `agents/hermes/dispatch/SOUL.md` | `agents/hermes/dispatch/config.snapshot.yaml` | `-t todo` (only the `todo` toolset), `--usage-file` |
| reviewer | Hermes profile `reviewer` | `agents/hermes/reviewer/SOUL.md` | `agents/hermes/reviewer/config.snapshot.yaml` | `-t todo`, `--usage-file` |
| builder | OpenClaw agent `builder` | `agents/openclaw/builder/AGENTS.md` | `agents/openclaw/openclaw.snapshot.json` (`agents.entries.builder`) | `agent --agent builder --json --timeout <s> --message-file <f>`, optional `--model` |
| tester | OpenClaw agent `tester` | `agents/openclaw/tester/AGENTS.md` | same file (`agents.entries.tester`) | same; `--model ollama-cloud/gpt-oss:120b` was used from ticket #4's recheck onward |

`agents/team.json` is the source of truth the setup script reads (models, fallbacks, roles, tool settings),
and `scripts/setup_agents.py` rebuilds the live configuration from it.

## What the snapshots contain

- **Models and fallback chains** exactly as configured. The tester's configured primary is `gpt-oss:20b`; the
  per-run override to `gpt-oss:120b` is a command-line flag, so it appears in the ledger, not in the config.
- **Execution and security settings:** the OpenClaw Docker sandbox (network `none`, read-only root, all
  capabilities dropped, 2 GB / 1 CPU / 128 PIDs, uid 1000), `exec` denied, elevated tools off, browser
  disabled, gateway on loopback with token authentication, the `coding` tool profile, and read-write access
  only to each agent's own workspace.
- **Skills:** none were written. The Hermes profiles were created with `--no-skills`; OpenClaw's bundled plugin
  skills (for example `browser-automation`) are unused because the browser is disabled.

## What is deliberately not included

Hermes `.env` and `auth.json`, the OpenClaw gateway token file, every API key and GitHub token, session
databases and memories. Fallback chains mention OpenRouter models, but **no OpenRouter key was ever configured**
and no reply from a non-cloud model was accepted (the orchestrator refuses them).

## How they were produced

`python3 scripts/snapshot_agent_configs.py` reads the live configuration, copies only whitelisted fields,
rewrites home-directory paths to `~`, and refuses to write if its own output contains anything secret-like
(token patterns, key blocks, cloud account identifiers, absolute user paths).
