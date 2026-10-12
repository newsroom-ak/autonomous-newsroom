#!/usr/bin/env bash
# Serve the newsroom app locally and expose ONLY it through a Cloudflare Quick Tunnel.
#
#   deploy/run-newsroom.sh [git-ref]      # default: origin/main
#
# What is exposed: one HTTP port (the FastAPI newsroom app) via a trycloudflare.com URL.
# What is not: OpenClaw (loopback gateway), Hermes, the Docker socket, the repository, tokens.
# The app runs from an immutable `git archive` of the ref in a hardened container (read-only
# root, no capabilities, non-root); the only secret it receives is OLLAMA_API_KEY, for summaries.
set -euo pipefail

REF="${1:-origin/main}"
PORT="${NEWSROOM_PORT:-8000}"
STATE_DIR="${NEWSROOM_STATE_DIR:-$HOME/newsroom-deploy}"
ENV_FILE="${NEWSROOM_CONFIG_DIR:-$HOME/.config/newsroom}/env"
IMAGE="${NEWSROOM_TEST_IMAGE:-newsroom-test-runner:1}"   # has the allowed packages preinstalled
NAME="newsroom-public"
CLOUDFLARED="${CLOUDFLARED:-$(command -v cloudflared || echo /opt/homebrew/opt/cloudflared/bin/cloudflared)}"

cd "$(git rev-parse --show-toplevel)"
CODE="$STATE_DIR/code-$(git rev-parse --short "$REF")"
mkdir -p "$STATE_DIR/data" "$CODE"
chmod 777 "$STATE_DIR/data"           # the container runs as uid 1000
git archive "$REF" app | tar -x -C "$CODE"

# Fail closed if the archive contains anything that looks like a secret.
if grep -R -E -l '(gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|sk-[A-Za-z0-9]{32,}|-----BEGIN [A-Z ]*PRIVATE KEY)' "$CODE" >/dev/null 2>&1; then
  echo "refusing to serve: secret-like string found in $CODE" >&2; exit 1
fi

[ -f "$ENV_FILE" ] || { echo "missing $ENV_FILE (needs OLLAMA_API_KEY)" >&2; exit 1; }
KEY="$(awk -F= '/^OLLAMA_API_KEY=/{print substr($0, index($0,"=")+1)}' "$ENV_FILE")"
[ -n "$KEY" ] || { echo "OLLAMA_API_KEY is empty in $ENV_FILE" >&2; exit 1; }

docker rm -f "$NAME" >/dev/null 2>&1 || true
OLLAMA_API_KEY="$KEY" docker run -d --name "$NAME" --restart unless-stopped \
  --user 1000:1000 --read-only --tmpfs /tmp --cap-drop ALL --security-opt no-new-privileges \
  --memory 1g --cpus 1 --pids-limit 256 \
  -p "127.0.0.1:$PORT:8000" \
  -v "$CODE/app:/work/app:ro" -v "$STATE_DIR/data:/data" -w /work/app \
  -e NEWSROOM_DB=/data/newsroom.db -e OLLAMA_API_KEY \
  "$IMAGE" python -m uvicorn newsroom.main:app --host 0.0.0.0 --port 8000 >/dev/null
unset KEY

# The app runs its first refresh (feed + up to 12 model calls) before it starts answering, which
# took about 70 s in the first live run; allow up to 5 minutes.
for _ in $(seq 1 150); do curl -fsS "http://127.0.0.1:$PORT/" >/dev/null 2>&1 && break; sleep 2; done
curl -fsS "http://127.0.0.1:$PORT/" >/dev/null || { echo "app did not become healthy" >&2; docker logs "$NAME" | tail -20 >&2; exit 1; }
echo "newsroom running locally on 127.0.0.1:$PORT (ref $(git rev-parse --short "$REF")); starting Quick Tunnel…"

# Quick Tunnel: no account, no config file; forwards the public URL to this single port only.
exec "$CLOUDFLARED" tunnel --no-autoupdate --url "http://127.0.0.1:$PORT"
