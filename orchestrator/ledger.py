"""Append-only JSONL trace of every handoff and every model call, with token usage."""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

from . import config

# Phases used by the token report (the brief asks for planning / coding / review-test / retries).
PHASES = ("planning", "coding", "review", "test", "retry-coding", "retry-review", "retry-test")


@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    api_calls: int = 0
    model: str | None = None
    provider: str | None = None
    estimated: bool = False


@dataclass
class Event:
    event: str  # e.g. plan, assign, build, handoff, review, test, escalate
    agent: str
    runtime: str
    ticket: int | None = None
    pr: int | None = None
    phase: str | None = None
    attempt: int | None = None
    outcome: str | None = None
    detail: str = ""
    usage: Usage | None = None
    duration_s: float | None = None
    ts: str = field(default_factory=lambda: time.strftime("%Y-%m-%dT%H:%M:%S%z"))


def append(ev: Event, path: Path | None = None) -> Event:
    path = path or config.LEDGER_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as fh:
        fh.write(json.dumps(asdict(ev), sort_keys=True) + "\n")
    return ev


def read(path: Path | None = None) -> list[dict]:
    path = path or config.LEDGER_FILE
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
