"""Token and model report from the ledger: per agent, per model, per phase."""

from __future__ import annotations

from collections import defaultdict

from . import ledger

PHASE_GROUPS = {
    "planning": "planning",
    "coding": "coding",
    "review": "review/test",
    "test": "review/test",
    "retry-coding": "retries",
    "retry-review": "retries",
    "retry-test": "retries",
}


def summarize(events: list[dict]) -> dict:
    by_agent: dict[str, int] = defaultdict(int)
    by_model: dict[str, int] = defaultdict(int)
    by_group: dict[str, int] = defaultdict(int)
    by_agent_group: dict[tuple[str, str], int] = defaultdict(int)
    calls = 0
    for e in events:
        u = e.get("usage")
        if not u:
            continue
        total = int(u.get("total_tokens") or 0) or int(u.get("input_tokens") or 0) + int(u.get("output_tokens") or 0)
        group = PHASE_GROUPS.get(e.get("phase") or "", "other")
        model = f"{u.get('provider') or '?'}/{u.get('model') or '?'}"
        by_agent[e["agent"]] += total
        by_model[model] += total
        by_group[group] += total
        by_agent_group[(e["agent"], group)] += total
        calls += 1
    return {"by_agent": dict(by_agent), "by_model": dict(by_model), "by_group": dict(by_group),
            "by_agent_group": {f"{a}|{g}": v for (a, g), v in by_agent_group.items()},
            "runs": calls, "total": sum(by_agent.values())}


def render_markdown(events: list[dict]) -> str:
    s = summarize(events)
    total = s["total"] or 1
    groups = ["planning", "coding", "review/test", "retries"]
    lines = ["# Token & model report", "",
             f"Source: `ledger.jsonl` ({s['runs']} agent runs). Token counts are those reported by "
             "each runtime (OpenClaw `agentMeta.usage`, Hermes `--usage-file`); not estimates.", "",
             "## Per agent and phase", "",
             "| Agent | " + " | ".join(groups) + " | Total | Share |",
             "|---|" + "---:|" * (len(groups) + 2)]
    for agent, tot in sorted(s["by_agent"].items(), key=lambda kv: -kv[1]):
        cells = [f"{s['by_agent_group'].get(f'{agent}|{g}', 0):,}" for g in groups]
        lines.append(f"| {agent} | " + " | ".join(cells) + f" | {tot:,} | {100 * tot / total:.0f}% |")
    lines += ["", "## Per model", "", "| Model | Tokens | Share |", "|---|---:|---:|"]
    for model, tot in sorted(s["by_model"].items(), key=lambda kv: -kv[1]):
        lines.append(f"| `{model}` | {tot:,} | {100 * tot / total:.0f}% |")
    lines += ["", "## Per phase", "", "| Phase | Tokens | Share |", "|---|---:|---:|"]
    for g in groups:
        v = s["by_group"].get(g, 0)
        lines.append(f"| {g} | {v:,} | {100 * v / total:.0f}% |")
    return "\n".join(lines) + "\n"


def main() -> str:
    return render_markdown(ledger.read())
