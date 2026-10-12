"""Invoke agents on their own runtimes and collect replies plus token usage."""

from __future__ import annotations

import json
import subprocess
import tempfile
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from . import config
from .ledger import Usage


@dataclass
class AgentReply:
    text: str
    usage: Usage
    duration_s: float
    ok: bool
    tools_used: list[str] = field(default_factory=list)
    session: str = ""
    error: str = ""


class AgentError(RuntimeError):
    pass


def run_openclaw(agent_id: str, message: str, session: str | None = None, timeout_s: int = 900,
                 model: str | None = None) -> AgentReply:
    """One agent turn through the local, token-authenticated OpenClaw gateway.

    The agent's sandbox, tool policy and model fallbacks come from the OpenClaw profile config;
    `model` overrides the primary model for this run only.
    """
    session = session or f"{agent_id}-{uuid.uuid4().hex[:8]}"
    with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False) as fh:
        fh.write(message)
        msg_path = fh.name
    started = time.monotonic()
    proc = subprocess.run(
        [config.OPENCLAW_BIN, "--profile", config.OPENCLAW_PROFILE, "agent", "--agent", agent_id,
         "--session-id", session, "--json", "--timeout", str(timeout_s), "--message-file", msg_path,
         *(["--model", model] if model else [])],
        env=config.agent_env(), capture_output=True, text=True, timeout=timeout_s + 120,
    )
    Path(msg_path).unlink(missing_ok=True)
    duration = time.monotonic() - started
    data = _first_json_object(proc.stdout)
    if data is None:
        raise AgentError(f"openclaw {agent_id}: no JSON output (rc={proc.returncode}): {proc.stderr.strip()[-800:]}")
    return parse_openclaw_result(data, duration, session)


def parse_openclaw_result(data: dict, duration: float, session: str) -> AgentReply:
    result = data.get("result") or {}
    meta = (result.get("meta") or {}).get("agentMeta") or {}
    usage_raw = meta.get("usage") or {}
    receipt = meta.get("terminalReceipt") or {}
    effective = receipt.get("effective") or {}
    usage = Usage(
        input_tokens=int(usage_raw.get("input") or 0),
        output_tokens=int(usage_raw.get("output") or 0),
        total_tokens=int(usage_raw.get("total") or 0),
        api_calls=int(meta.get("assistantTurns") or 0),
        model=effective.get("model") or meta.get("model"),
        provider=effective.get("provider") or meta.get("provider"),
    )
    text = "\n".join(p.get("text") or "" for p in result.get("payloads") or []).strip()
    ok = data.get("status") == "ok"
    err = "" if ok else json.dumps(data.get("error") or data.get("summary"))[:800]
    return AgentReply(text=text, usage=usage, duration_s=duration, ok=ok,
                      tools_used=list(receipt.get("successfulToolNames") or []), session=session, error=err)


def run_hermes(profile: str, prompt: str, toolsets: str = "todo", timeout_s: int = 900) -> AgentReply:
    """One-shot Hermes run in an empty working directory (so no repo AGENTS.md is injected).

    `toolsets` must be non-empty: an empty value makes Hermes load its default tool set.
    """
    if not toolsets:
        raise ValueError("toolsets must be explicit; empty means Hermes defaults")
    cwd = config.WORK_DIR / "hermes" / profile
    cwd.mkdir(parents=True, exist_ok=True)
    usage_file = cwd / f"usage-{uuid.uuid4().hex[:8]}.json"
    started = time.monotonic()
    proc = subprocess.run(
        [config.HERMES_BIN, "-p", profile, "-z", prompt, "-t", toolsets, "--usage-file", str(usage_file)],
        cwd=cwd, env=config.agent_env(), capture_output=True, text=True, timeout=timeout_s,
    )
    duration = time.monotonic() - started
    report = json.loads(usage_file.read_text()) if usage_file.exists() else {}
    usage_file.unlink(missing_ok=True)
    return parse_hermes_result(proc.stdout, proc.returncode, proc.stderr, report, duration)


def parse_hermes_result(stdout: str, rc: int, stderr: str, report: dict, duration: float) -> AgentReply:
    usage = Usage(
        input_tokens=int(report.get("input_tokens") or 0),
        output_tokens=int(report.get("output_tokens") or 0),
        total_tokens=int((report.get("total_including_auxiliary") or {}).get("total_tokens") or report.get("total_tokens") or 0),
        api_calls=int((report.get("total_including_auxiliary") or {}).get("api_calls") or report.get("api_calls") or 0),
        model=report.get("model"),
        provider=report.get("provider"),
    )
    ok = rc == 0 and not report.get("failed", False)
    err = "" if ok else (report.get("failure") or stderr.strip()[-800:] or f"exit {rc}")
    return AgentReply(text=stdout.strip(), usage=usage, duration_s=duration, ok=ok,
                      session=report.get("session_id") or "", error=err)


def _first_json_object(text: str) -> dict | None:
    start = text.find("{")
    while start != -1:
        try:
            obj, _ = json.JSONDecoder().raw_decode(text[start:])
            if isinstance(obj, dict):
                return obj
        except json.JSONDecodeError:
            pass
        start = text.find("{", start + 1)
    return None


def extract_json(text: str) -> dict:
    """Pull the JSON object out of a model reply (tolerates ```json fences and stray prose)."""
    if "```" in text:
        for chunk in text.split("```")[1::2]:
            chunk = chunk.removeprefix("json").strip()
            obj = _first_json_object(chunk)
            if obj is not None:
                return obj
    obj = _first_json_object(text)
    if obj is None:
        raise AgentError(f"agent reply contained no JSON object: {text[:300]!r}")
    return obj
