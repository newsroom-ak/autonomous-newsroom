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
    sub.add_parser("review").add_argument("ticket", type=int)
    b = sub.add_parser("build")
    b.add_argument("ticket", type=int)
    b.add_argument("--max-turns", type=int, help="approximate turn budget (prompt + wall-clock deadline)")
    t = sub.add_parser("test")
    t.add_argument("ticket", type=int)
    t.add_argument("--focus-file", help="file listing the only tests to add (targeted re-test)")
    t.add_argument("--keep-existing", action="store_true", help="keep the tester's previous acceptance file")
    t.add_argument("--model", help="one-run primary model override, e.g. ollama-cloud/gpt-oss:120b")
    t.add_argument("--max-turns", type=int, help="approximate turn budget (prompt + wall-clock deadline)")
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
    elif args.cmd == "review":
        print(orch.review(args.ticket))
    elif args.cmd == "build":
        print(orch.build(args.ticket, max_turns=args.max_turns))
    elif args.cmd == "test":
        focus = open(args.focus_file).read() if args.focus_file else None
        print(orch.test(args.ticket, focus=focus, keep_existing=args.keep_existing, model=args.model,
                        max_turns=args.max_turns))
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
