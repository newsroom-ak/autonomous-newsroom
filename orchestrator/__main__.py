"""CLI: python3 -m orchestrator <command>

  plan              dispatch turns agents/shared/brief.md into GitHub issues
  assign            dispatch picks the next ticket for the builder
  build N           builder implements ticket N and opens/updates its PR
  review N          reviewer reviews ticket N's PR and sets agent/review
  test N            tester writes acceptance tests; harness runs them offline; sets agent/test
  step              advance the board by one agent action
  run [--max N]     repeat `step` until idle or N steps
  report            print the token & model report from the ledger
"""

from __future__ import annotations

import argparse
import sys

from . import report
from .workflow import Orchestrator


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="orchestrator", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("plan")
    sub.add_parser("assign")
    for name in ("build", "review", "test"):
        sub.add_parser(name).add_argument("ticket", type=int)
    sub.add_parser("step")
    r = sub.add_parser("run")
    r.add_argument("--max", type=int, default=20)
    sub.add_parser("report")
    args = p.parse_args(argv)

    if args.cmd == "report":
        print(report.main())
        return 0
    orch = Orchestrator()
    if args.cmd == "plan":
        print(orch.plan())
    elif args.cmd == "assign":
        print(orch.assign())
    elif args.cmd in ("build", "review", "test"):
        print(getattr(orch, args.cmd)(args.ticket))
    elif args.cmd == "step":
        print(orch.step())
    elif args.cmd == "run":
        for i in range(args.max):
            result = orch.step()
            print(f"[step {i + 1}] {result}", flush=True)
            if result.startswith("idle") or "needs-human" in result:
                break
    return 0


if __name__ == "__main__":
    sys.exit(main())
