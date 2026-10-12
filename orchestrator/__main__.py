"""CLI: python3 -m orchestrator <command>

  plan              dispatch turns agents/shared/brief.md into GitHub issues
  assign            dispatch picks the next ticket for the builder
  build N           builder implements ticket N and opens/updates its PR
  review N          reviewer reviews ticket N's PR and sets agent/review
  test N            tester writes acceptance tests; harness commits them to the PR, runs all
                    committed tests offline and sets agent/test (then review)
  publish-tests N --from FILE --written-in TEXT
                    commit an existing tester-written test file to the PR (no model call)
  verify N          re-run the committed tests offline and set agent/test (no model call)
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
    b.add_argument("--exception", help="human-authorized reason for one attempt beyond max_attempts")
    b.add_argument("--scope", action="append", help="only import these paths from the builder (repeatable)")
    b.add_argument("--task-file", help="narrow task that replaces the full ticket in TICKET.md")
    t = sub.add_parser("test")
    t.add_argument("ticket", type=int)
    t.add_argument("--focus-file", help="file listing the only tests to add (targeted re-test)")
    t.add_argument("--keep-existing", action="store_true", help="keep the tester's previous acceptance file")
    t.add_argument("--model", help="one-run primary model override, e.g. ollama-cloud/gpt-oss:120b")
    t.add_argument("--max-turns", type=int, help="approximate turn budget (prompt + wall-clock deadline)")
    pt = sub.add_parser("publish-tests")
    pt.add_argument("ticket", type=int)
    pt.add_argument("--from", dest="source", required=True, help="tester-written test file to publish verbatim")
    pt.add_argument("--written-in", required=True, help="tester session(s) and model that wrote the file")
    pt.add_argument("--update", action="append", default=[], metavar="REL=FILE",
                    help="tester-written update to an earlier acceptance file, e.g. app/tests/acceptance/test_ticket_4.py=/path")
    v = sub.add_parser("verify")
    v.add_argument("ticket", type=int)
    v.add_argument("--smoke", action="store_true",
                   help="run existing committed tests before the tester has written this ticket's tests")
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
        print(orch.build(args.ticket, max_turns=args.max_turns, exception=args.exception, scope=args.scope,
                         task=open(args.task_file).read() if args.task_file else None))
    elif args.cmd == "test":
        focus = open(args.focus_file).read() if args.focus_file else None
        print(orch.test(args.ticket, focus=focus, keep_existing=args.keep_existing, model=args.model,
                        max_turns=args.max_turns))
    elif args.cmd == "publish-tests":
        updates = {rel: open(src).read() for rel, src in (u.split("=", 1) for u in args.update)}
        print(orch.publish_tests(args.ticket, open(args.source).read(), args.written_in, updates=updates))
    elif args.cmd == "verify":
        print(orch.verify(args.ticket, smoke=args.smoke))
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
