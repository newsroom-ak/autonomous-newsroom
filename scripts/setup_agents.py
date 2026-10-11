#!/usr/bin/env python3
"""Idempotently configure the newsroom agent team from agents/team.json.

- Hermes: profiles `dispatch` and `reviewer` get their SOUL.md, primary model and fallback chain.
- OpenClaw: agents `builder` and `tester` get a workspace, prompt (AGENTS.md), model chain,
  the coding tool profile and a sandbox that mounts only their own workspace read-write.
  The profile's security defaults (sandbox for all sessions, network none, read-only root,
  cap-drop ALL, exec denied, elevated tools off, loopback token-auth gateway) are checked,
  never relaxed.
- Builds the offline test-runner image.

Secrets are never read here; only the presence of the key/token files is reported.

Usage: python3 scripts/setup_agents.py [--check]
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from orchestrator import config, workspace  # noqa: E402

TEAM = json.loads(config.TEAM_FILE.read_text())
OC_CONFIG = Path.home() / f".openclaw-{config.OPENCLAW_PROFILE}" / "openclaw.json"
OC_TEMPLATE = REPO / "agents" / "openclaw" / "openclaw.base.json"
REQUIRED_DEFAULTS = {
    ("agents", "defaults", "sandbox", "mode"): "all",
    ("agents", "defaults", "sandbox", "docker", "network"): "none",
    ("agents", "defaults", "sandbox", "docker", "readOnlyRoot"): True,
    ("agents", "defaults", "sandbox", "docker", "capDrop"): ["ALL"],
    ("tools", "exec", "security"): "deny",
    ("tools", "elevated", "enabled"): False,
    ("gateway", "bind"): "loopback",
    ("gateway", "auth", "mode"): "token",
}


def run(*cmd: str, check: bool = True, env: dict | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, check=check, env=env)


def oc(*args: str) -> subprocess.CompletedProcess:
    return run(config.OPENCLAW_BIN, "--profile", config.OPENCLAW_PROFILE, *args, env=config.agent_env())


def hermes(profile: str, *args: str) -> subprocess.CompletedProcess:
    return run(config.HERMES_BIN, "-p", profile, *args, env=config.agent_env())


def dig(d: dict, path: tuple) -> object:
    for k in path:
        if not isinstance(d, dict) or k not in d:
            return None
        d = d[k]
    return d


def check_prereqs() -> list[str]:
    problems = []
    node = Path(config.NODE_BIN_DIR) / "node"
    if not node.exists():
        problems.append(f"Node 24.16+/26.1+ not found at {node}")
    else:
        major = int(run(str(node), "--version").stdout.strip().lstrip("v").split(".")[0])
        if major < 24:
            problems.append(f"Node {major} is too old for OpenClaw")
    for name, path in (("openclaw", config.OPENCLAW_BIN), ("hermes", config.HERMES_BIN)):
        if not Path(path).exists():
            problems.append(f"{name} not found at {path}")
    for tool in ("docker", "gh", "git"):
        if shutil.which(tool) is None:
            problems.append(f"{tool} not on PATH")
    return problems


def setup_hermes(check_only: bool) -> None:
    existing = run(config.HERMES_BIN, "profile", "list").stdout
    for name, spec in TEAM["agents"].items():
        if spec["runtime"] != "hermes":
            continue
        profile = spec["profile"]
        if profile not in existing:
            if check_only:
                print(f"  ! hermes profile {profile} missing")
                continue
            run(config.HERMES_BIN, "profile", "create", profile, "--no-skills", "--description", spec["role"])
        home = Path.home() / ".hermes" / "profiles" / profile
        if check_only:
            print(f"  hermes:{profile} model={hermes(profile, 'config', 'get', 'model.default').stdout.strip()}")
            continue
        shutil.copyfile(REPO / "agents" / "hermes" / profile / "SOUL.md", home / "SOUL.md")
        primary = spec["model"]
        hermes(profile, "config", "set", "model.provider", primary["provider"])
        hermes(profile, "config", "set", "model.default", primary["model"])
        if primary.get("base_url"):
            hermes(profile, "config", "set", "model.base_url", primary["base_url"])
        else:  # a stale local base_url would silently override the cloud provider
            for key in ("model.base_url", "model.ollama_num_ctx"):
                run(config.HERMES_BIN, "-p", profile, "config", "unset", key, check=False)
        # Fallback chain lives in config.yaml as a list; written via the YAML file directly.
        set_hermes_fallbacks(home / "config.yaml", spec["fallbacks"])
        print(f"  hermes:{profile} -> {primary['provider']}/{primary['model']} (+{len(spec['fallbacks'])} fallbacks)")


def set_hermes_fallbacks(cfg: Path, fallbacks: list[dict]) -> None:
    lines = cfg.read_text().splitlines()
    out, skipping = [], False
    for line in lines:
        if line.startswith("fallback_model:"):
            skipping = True
            continue
        if skipping and (line.startswith(" ") or line.startswith("-") or not line.strip()):
            continue
        skipping = False
        out.append(line)
    out.append("fallback_model:")
    for fb in fallbacks:
        out.append(f"  - provider: {json.dumps(fb['provider'])}")
        out.append(f"    model: {json.dumps(fb['model'])}")
        if fb.get("base_url"):
            out.append(f"    base_url: {json.dumps(fb['base_url'])}")
    cfg.write_text("\n".join(out) + "\n")


def setup_openclaw(check_only: bool) -> None:
    if not OC_CONFIG.exists():
        if check_only:
            print(f"  ! {OC_CONFIG} missing")
            return
        OC_CONFIG.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(OC_TEMPLATE, OC_CONFIG)
        print(f"  created {OC_CONFIG} from template; run `openclaw --profile {config.OPENCLAW_PROFILE} onboard` "
              "to generate the gateway token secret")
    cfg = json.loads(OC_CONFIG.read_text())
    for path, want in REQUIRED_DEFAULTS.items():
        got = dig(cfg, path)
        status = "ok" if got == want else f"MISMATCH (got {got!r})"
        print(f"  openclaw {'.'.join(path)} = {want!r}: {status}")
        if got != want:
            raise SystemExit("OpenClaw security defaults differ from the verified baseline; refusing to continue.")
    if check_only:
        return
    oc_agents = [spec for spec in TEAM["agents"].values() if spec["runtime"] == "openclaw"]
    # `agents add` rewrites the config file, so add every missing agent first and only then
    # load the config and apply settings (otherwise earlier in-memory edits are lost).
    existing = set(cfg.get("agents", {}).get("entries", {}))
    for spec in oc_agents:
        ws = config.WORK_DIR / spec["agent_id"]
        ws.mkdir(parents=True, exist_ok=True)
        if spec["agent_id"] not in existing:
            oc("agents", "add", spec["agent_id"], "--workspace", str(ws), "--model", spec["model"], "--non-interactive", "--json")
    cfg = json.loads(OC_CONFIG.read_text())
    entries = cfg["agents"]["entries"]
    for spec in oc_agents:
        agent_id = spec["agent_id"]
        ws = config.WORK_DIR / agent_id
        e = entries[agent_id]
        e["description"] = spec["role"]
        e["model"] = {"primary": spec["model"], "fallbacks": spec["fallbacks"]}
        e["tools"] = {"profile": "coding"}
        e["sandbox"] = {"workspaceAccess": "rw", "scope": "agent"}
        # The workspace is agent-writable: never follow a symlink planted there.
        workspace.safe_write(ws / "AGENTS.md", (REPO / "agents" / "openclaw" / agent_id / "AGENTS.md").read_text())
        (ws / "BOOTSTRAP.md").unlink(missing_ok=True)
        print(f"  openclaw:{agent_id} -> {spec['model']} (+{len(spec['fallbacks'])} fallbacks), workspace {ws}")
    ensure_local_models(cfg)
    OC_CONFIG.write_text(json.dumps(cfg, indent=2) + "\n")
    print("  " + oc("config", "validate").stdout.strip())


def ensure_local_models(cfg: dict) -> None:
    """Register the local Ollama fallback models so OpenClaw can route to them."""
    models = cfg.setdefault("models", {}).setdefault("providers", {}).setdefault("ollama", {
        "baseUrl": "http://127.0.0.1:11434", "api": "ollama", "apiKey": "ollama-local", "models": []})["models"]
    have = {m["id"] for m in models}
    for spec in TEAM["agents"].values():
        for ref in [spec.get("model")] + list(spec.get("fallbacks") or []):
            if isinstance(ref, str) and ref.startswith("ollama/") and ref[7:] not in have:
                models.append({"id": ref[7:], "name": ref[7:], "reasoning": False, "input": ["text"],
                               "contextWindow": 32768, "maxTokens": 4096, "params": {"num_ctx": 32768}})
                have.add(ref[7:])


def report_secrets() -> None:
    env_keys = set(config.read_env_file())
    for key in ("OLLAMA_API_KEY", "OPENROUTER_API_KEY"):
        print(f"  {key}: {'present' if key in env_keys else 'MISSING'} in {config.ENV_FILE}")
    for name, spec in TEAM["agents"].items():
        p = config.TOKENS_DIR / spec["token_file"]
        state = "MISSING"
        if p.exists():
            mode = p.stat().st_mode & 0o777
            state = "present" + ("" if mode == 0o600 else f" (permissions {oct(mode)}, expected 0o600)")
        print(f"  GitHub token for {name}: {state} ({p})")


def build_test_image() -> None:
    r = run("docker", "build", "-q", "-t", config.TEST_IMAGE, str(REPO / "docker" / "test-runner"), check=False)
    print(f"  test runner image {config.TEST_IMAGE}: {'built' if r.returncode == 0 else 'FAILED ' + r.stderr[-300:]}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="report state without changing anything")
    args = ap.parse_args()
    print("prerequisites")
    problems = check_prereqs()
    for p in problems:
        print(f"  ! {p}")
    if problems:
        return 1
    print("  ok")
    print("hermes")
    setup_hermes(args.check)
    print("openclaw")
    setup_openclaw(args.check)
    if not args.check:
        print("docker")
        build_test_image()
    print("secrets (presence only)")
    report_secrets()
    return 0


if __name__ == "__main__":
    sys.exit(main())
