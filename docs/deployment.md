# Deployment

## Where it runs, and why

**Plan A (Oracle Cloud Always Free, Terraform) did not complete.** `infra/` contains the
Terraform for one `VM.Standard.A1.Flex` instance plus its network. The network resources
(VCN, subnet, internet gateway, route table, security list) were created, but instance creation
failed with *Out of host capacity* in the home region, and was not retried successfully. The
local Terraform state is intentionally untouched and git-ignored.

**Plan B (used): the application runs on the engineer's Mac, exposed with a Cloudflare Quick
Tunnel**, the emergency fallback the assessment brief describes.

### Limitations (disclosed)

- The public `*.trycloudflare.com` URL **changes every time the tunnel restarts**; Quick Tunnels
  have no uptime guarantee.
- The Mac must stay **awake and online** for the URL to work.
- The agent runtimes (OpenClaw, Hermes) and the application run on the same machine, but only the
  application is exposed.

## What is exposed

`deploy/run-newsroom.sh` runs the FastAPI app in a hardened container (read-only root, all Linux
capabilities dropped, non-root user, memory/CPU/PID limits) bound to `127.0.0.1:8000`, built from
an immutable `git archive` of a chosen commit, and points `cloudflared` at that single port.

| Exposed | Not exposed |
|---|---|
| The newsroom page (`/`) and JSON API (`/api/articles`) | OpenClaw gateway (loopback, token auth), Hermes |
| | Docker daemon/socket, the repository, `~/.config/newsroom/` (keys, GitHub tokens) |

The only secret the app receives is `OLLAMA_API_KEY` (for article summaries), as an environment
variable of the container. The script refuses to start if the archived code contains
secret-like strings.

## Observed in the first live run (2026-10-12)

- Against the real Google News feed and Ollama Cloud, the merged code stored 100 articles and
  summarized 12 (the per-refresh cap): 11 model summaries, 1 title fallback, none empty.
- **Slow first start:** the scheduler awaits its first refresh before the server begins to answer
  (about 70 s), so the site is unreachable right after a restart until that refresh completes.
- FastAPI's auto-generated `/docs`, `/redoc` and `/openapi.json` are reachable (they describe the two
  read-only routes); there are no write endpoints.
- Reasoning models sometimes use the whole 800-token budget on hidden reasoning and return empty
  content; the app rejects those replies and falls back to the second model, then to the marked
  title fallback.

## Reproduce

Prerequisites: Docker, `cloudflared` (`brew install cloudflared`), a checkout of this
repository, and `~/.config/newsroom/env` (mode 600) containing `OLLAMA_API_KEY=...`.

```bash
python3 scripts/setup_agents.py        # only needed to rebuild the offline test image
docker build -t newsroom-test-runner:1 docker/test-runner
deploy/run-newsroom.sh origin/main     # prints the trycloudflare.com URL (look for "https://...")
```

The scheduler refreshes the feed at startup and then every `NEWSROOM_REFRESH_MINUTES`
(default 180). Stop everything with Ctrl-C and `docker rm -f newsroom-public`.

## Oracle retry (optional, human decision)

`infra/` can be re-applied later (try another availability domain or 1 OCPU / 6 GB per the
brief). This changes the Terraform state and is not part of the fallback.
