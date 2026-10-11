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

# Variables that must never reach an agent runtime or the test container.
SECRET_ENV_PREFIXES = ("GH_", "GITHUB_", "NEWSROOM_PUSH_TOKEN")


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


def read_env_file(path: Path = ENV_FILE) -> dict[str, str]:
    """Parse KEY=VALUE lines. Values are returned to the caller and never logged."""
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


def agent_env(extra: dict[str, str] | None = None) -> dict[str, str]:
    """Environment for agent runtimes: model keys yes, GitHub credentials no."""
    env = {k: v for k, v in os.environ.items() if not k.startswith(SECRET_ENV_PREFIXES)}
    env.update(read_env_file())
    env["PATH"] = f"{NODE_BIN_DIR}:{Path(HERMES_BIN).parent}:{env.get('PATH', '')}"
    if extra:
        env.update(extra)
    return env


class MissingToken(RuntimeError):
    pass


def github_token(agent_name: str, team: Team) -> str:
    """Read one agent's fine-grained token. Raises instead of falling back to another identity."""
    path = TOKENS_DIR / team.agent(agent_name)["token_file"]
    if not path.exists():
        raise MissingToken(f"GitHub token for agent '{agent_name}' not found at {path}")
    token = path.read_text().strip()
    if not token:
        raise MissingToken(f"GitHub token file for agent '{agent_name}' is empty: {path}")
    return token
