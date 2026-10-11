#!/usr/bin/env bash
# Run the OpenClaw gateway (foreground, loopback only, token auth from the profile config)
# with the model API keys from ~/.config/newsroom/env in its environment.
# GitHub tokens are deliberately removed from the environment: OpenClaw agents never get them.
set -euo pipefail

NODE_BIN="${NEWSROOM_NODE_BIN:-/opt/homebrew/opt/node/bin}"
OPENCLAW_BIN="${OPENCLAW_BIN:-$HOME/agents/openclaw-runtime/node_modules/.bin/openclaw}"
PROFILE="${OPENCLAW_PROFILE:-confer-newsroom}"
ENV_FILE="${NEWSROOM_CONFIG_DIR:-$HOME/.config/newsroom}/env"

export PATH="$NODE_BIN:$PATH"
if [[ -f "$ENV_FILE" ]]; then
  set -a
  # shellcheck disable=SC1090
  . "$ENV_FILE"
  set +a
fi
unset GH_TOKEN GITHUB_TOKEN NEWSROOM_PUSH_TOKEN

exec "$OPENCLAW_BIN" --profile "$PROFILE" gateway run
