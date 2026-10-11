# Trace: ticket #4 (database schema and feed fetching), PR #9

All entries below are real. Ticket and PR comments are on GitHub
([#4](https://github.com/newsroom-ak/autonomous-newsroom/issues/4),
[PR #9](https://github.com/newsroom-ak/autonomous-newsroom/pull/9)); token counts come from
`~/newsroom-work/ledger.jsonl`, written from each runtime's own usage report.

## Timeline (2026-10-11, local time)

| Time | Event | Agent · model | Attempt | Outcome | Turns | Tokens (in / out / runtime total) |
|---|---|---|---|---|---:|---|
| 15:20 | plan | dispatch · nemotron-3-ultra | – | created #4–#8 | 2 | 3,315 / 1,488 / 5,288 |
| 15:21 | assign | dispatch · nemotron-3-ultra | 1 | #4 → builder | 2 | 2,451 / 234 / 3,188 |
| 15:23 | build | builder · gpt-oss:120b | 1 | commit `b447087`, PR #9 | 23 | 8,222 / 9,126 / 257,476 |
| 15:28 | review | reviewer · nemotron-3-super | 1 | **approve** `b447087` (missed criterion 6) | 2 | 7,647 / 3,065 / 11,211 |
| 15:31 | test | tester · gpt-oss:20b | 1 | **pass** `b447087`, 5 tests (weak coverage) | 35 | 61,521 / 9,355 / 488,192 |
| 15:3x | human | Ankita2525 | – | quality recheck: #4 back to `status:in-test` | – | – |
| 15:38 | test (recheck) | tester · gpt-oss:120b | 1 | fail `b447087`, not a defect (see below) | 9 | 16,328 / 5,978 / 99,922 |
| 15:3x | human | Ankita2525 | – | review of tester's tests posted on PR #9 | – | – |
| 15:39 | test (recheck) | tester · gpt-oss:120b | 1 | **fail** `b447087`: `SystemExit` escapes `_run_refresh` | 4 | 11,552 / 1,035 / 74,123 |
| 15:40 | assign | dispatch · nemotron-3-ultra | 2 | #4 → builder with failure | 2 | 5,872 / 453 / 6,820 |
| 15:41 | build | builder · gpt-oss:120b | 2 | commit `3f96ac7` (refresh.py only) | 11 | 11,090 / 3,791 / 124,785 |
| 15:42 | review | reviewer · nemotron-3-super | 2 | **approve** `3f96ac7` (missed HTTP 500 gap) | 2 | 7,911 / 4,026 / 12,435 |
| 15:43 | test | tester · gpt-oss:120b | 2 | **fail** `3f96ac7`: HTTP 500 still calls `sys.exit(1)` | 9 | 7,134 / 3,305 / 85,783 |

Runtime totals: 1,169,223 tokens for this ticket so far. For OpenClaw runs the runtime total
is much larger than input + output; it appears to count the full prompt (system prompt +
tool schemas, ~7–11K tokens) again on every turn. This is an interpretation, not confirmed
by OpenClaw documentation; `openclaw gateway usage-cost` returned zeros for this profile.

## What broke and how it was caught

1. **Reviewer approved a missed acceptance criterion (attempt 1).** Criterion 6 asks for an
   offline test that mocks the HTTP feed response. The builder's tests monkeypatch
   `feed.fetch_articles` itself, so RSS parsing never runs. The reviewer's summary claimed
   "offline tests mock HTTP". Caught by the human engineer reading the diff.
2. **Cheap tester model wrote weak tests (attempt 1).** `gpt-oss:20b` used 35 turns /
   488K tokens and produced 3 tests, none covering parsing, malformed feeds or network
   failures, despite the QA policy in its prompt. Caught by the human engineer reading the
   posted test file.
3. **Human quality recheck.** #4 was moved back to `status:in-test` by the human account with
   a comment on #4 and PR #9. Original passing statuses were left in place as history.
4. **Tester's first recheck encoded the defect.** With `gpt-oss:120b` and a ~12-turn budget
   the tester added real RSS-parsing, malformed-feed, invalid-XML and network tests, but its
   network test asserted `pytest.raises(SystemExit)` (accepting the defect) and another
   asserted an undefined counting rule (`articles_fetched == 3`). The resulting failure was
   real pytest output but not a defect, so it was not sent to the builder. The human posted
   a review on PR #9 and the tester corrected only those two tests
   (`agents/shared/rechecks/ticket-4-focus-2.md`).
5. **Genuine defect found.** The corrected test failed on `b447087`:
   `newsroom/refresh.py:66: in _run_refresh … SystemExit: 1` on a connection error. This
   would kill the in-app scheduler planned in ticket #8.
6. **Builder repair was incomplete.** Attempt 2 (`3f96ac7`) stops exiting only for
   `httpx.RequestError`. `httpx.HTTPStatusError` (HTTP 500) is not a subclass of it, and any
   other exception still exits; its code comment says it mirrors what "the test suite only
   triggers". The reviewer approved it; the tester's re-run caught it:
   `newsroom/refresh.py:84: in _run_refresh … SystemExit: 1` after `HTTPStatusError: 500`.

## State at the end of the recheck

- PR #9 head `3f96ac7`: `agent/review` success, `agent/test` failure. Not mergeable under the
  ruleset; not ready for human approval.
- Ticket #4: `status:changes-requested`, builder attempts used: 2 of 3.

## Changes made because of this trace

- Harness refuses replies from non-cloud providers (no silent local fallback).
- Tester QA policy: mock at the HTTP boundary; cover malformed input and network failures.
- Reviewer rule: verify claims about tests against the diff.
- Focused re-test mode, approximate turn budgets, one-run model override.
- Lesson: the reviewer sees only the diff, not the tester's acceptance tests, and
  `nemotron-3-super` approves generically. A stronger reviewer model or giving it the
  latest test output is the next thing to try.
