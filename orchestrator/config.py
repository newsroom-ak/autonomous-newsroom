"""Team roster, filesystem layout and secret loading."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
TEAM_FILE = REPO_ROOT / "agents" / "team.json"
BRIEF_FILE = REPO_ROOT / "agents" / "shared" / "brief.md"

WORK_DIR = Path(os.environ.get("NEWSROOM_WORK_DIR", Path.home() / "newsroom-work"))
CONFIG_DIR = Path(os.environ.get("NEWSROOM_CONFIG_DIR", Path.home() / ".config" / "newsroom"))
TOKENS_DIR = CONFIG_DIR / "tokens"
ENV_FILE = CONFIG_DIR / "env"
LEDGER_FILE = WORK_DIR / "ledger.jsonl"

OPENCLAW_BIN = os.environ.get(
    "OPENCLAW_BIN", str(Path.home() / "agents" / "openclaw-runtime" / "node_modules" / ".bin" / "openclaw")
)
OPENCLAW_PROFILE = os.environ.get("OPENCLAW_PROFILE", "confer-newsroom")
NODE_BIN_DIR = os.environ.get("NEWSROOM_NODE_BIN", "/opt/homebrew/opt/node/bin")
HERMES_BIN = os.environ.get("HERMES_BIN", str(Path.home() / ".local" / "bin" / "hermes"))
TEST_IMAGE = os.environ.get("NEWSROOM_TEST_IMAGE", "newsroom-test-runner:1")

# Subprocess environments are built from an allowlist, never by copying os.environ: the
# operator's shell may hold unrelated credentials that must not reach agents or gh/git.
BASE_ENV_KEYS = ("HOME", "USER", "LOGNAME", "SHELL", "LANG", "LC_ALL", "LC_CTYPE", "TERM", "TMPDIR",
                 "PATH", "DOCKER_HOST", "DOCKER_CONTEXT")
# The only secrets agent runtimes may receive (model keys from ENV_FILE).
MODEL_KEY_NAMES = ("OLLAMA_API_KEY", "OPENROUTER_API_KEY", "GEMINI_API_KEY", "GROQ_API_KEY")


@dataclass
class Team:
    repo: str
    base_branch: str
    human_reviewer: str
    bot_login: str
    bot_email: str
    max_attempts: int
    allowed_paths: list[str]
    agents: dict[str, dict] = field(default_factory=dict)

    def agent(self, name: str) -> dict:
        return self.agents[name]


def load_team(path: Path = TEAM_FILE) -> Team:
    data = json.loads(path.read_text())
    return Team(**data)


def read_env_file(path: Path | None = None) -> dict[str, str]:
    """Parse KEY=VALUE lines. Values are returned to the caller and never logged."""
    path = path or ENV_FILE
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.removeprefix("export ").strip()
        values[key] = value.strip().strip('"').strip("'")
    return values


def base_env() -> dict[str, str]:
    return {k: os.environ[k] for k in BASE_ENV_KEYS if k in os.environ}


def agent_env(extra: dict[str, str] | None = None) -> dict[str, str]:
    """Environment for agent runtimes: allowlisted basics plus model keys; no GitHub credentials."""
    env = base_env()
    env.update({k: v for k, v in read_env_file().items() if k in MODEL_KEY_NAMES})
    env["PATH"] = f"{NODE_BIN_DIR}:{Path(HERMES_BIN).parent}:{env.get('PATH', '/usr/bin:/bin')}"
    if extra:
        env.update(extra)
    return env


def github_env(token: str) -> dict[str, str]:
    """Environment for gh/git acting as one agent.

    - GH_CONFIG_DIR points at an empty directory so gh can never fall back to the human's login.
    - Global/system git config is ignored, so an `insteadOf` rewrite or credential helper in the
      operator's config cannot redirect or capture the token.
    """
    empty = WORK_DIR / "empty-gh-config"
    empty.mkdir(parents=True, exist_ok=True, mode=0o700)
    env = base_env()
    env.update({
        "GH_TOKEN": token, "GH_CONFIG_DIR": str(empty), "GH_PROMPT_DISABLED": "1", "GH_NO_UPDATE_NOTIFIER": "1",
        "NEWSROOM_PUSH_TOKEN": token, "GIT_TERMINAL_PROMPT": "0",
        "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1",
    })
    return env


class MissingToken(RuntimeError):
    pass


def github_token(agent_name: str, team: Team) -> str:
    """Read one agent's fine-grained token. Raises instead of falling back to another identity."""
    path = TOKENS_DIR / team.agent(agent_name)["token_file"]
    if not path.exists():
        raise MissingToken(f"GitHub token for agent '{agent_name}' not found at {path}")
    st = path.lstat()
    if path.is_symlink() or st.st_uid != os.getuid() or st.st_mode & 0o077:
        raise MissingToken(f"refusing {path}: must be a regular file owned by you with mode 600")
    token = path.read_text().strip()
    if not token:
        raise MissingToken(f"GitHub token file for agent '{agent_name}' is empty: {path}")
    return token
