# Issue #25: evidence index

**Ticket:** [#25 Improve Newsroom Tagging and Summarization with Validated Structured LLM Output](https://github.com/newsroom-ak/autonomous-newsroom/issues/25)
**Pull request:** [#26](https://github.com/newsroom-ak/autonomous-newsroom/pull/26), merged into `main` as [`45504f4`](https://github.com/newsroom-ak/autonomous-newsroom/commit/45504f45808aceda523e3d5ddac923f5d878f544); final head [`94d4c06`](https://github.com/newsroom-ak/autonomous-newsroom/commit/94d4c06994822a813645fc4b16d42b0f6c5c77f5)

This is a curated summary of one ticket's delivery by the Hermes and OpenClaw agent team, including the failures and the
human involvement. All times are UTC on 2026-10-15 and come from the orchestrator ledger
([`ledger-ticket-25.jsonl`](ledger-ticket-25.jsonl), the verbatim 19 events for this ticket) and from GitHub. Every token figure
is **as reported by the agent runtimes**, never estimated.

**Withheld on purpose:** the raw agent session transcripts (which include stored model reasoning and tool traces) and the full
task and focus files. The human-written instructions that changed what the agents were asked to do are quoted in section 7.

## 1. Result

| | |
|---|---|
| Issue #25 | closed as completed, 19:48:30Z |
| PR #26 | merged 19:48:29Z by `Ankita2525`, merge commit [`45504f4`](https://github.com/newsroom-ak/autonomous-newsroom/commit/45504f45808aceda523e3d5ddac923f5d878f544); **no ruleset bypass** (human approval at 19:47:43Z plus both agent gates) |
| Final gates on [`94d4c06`](https://github.com/newsroom-ak/autonomous-newsroom/commit/94d4c06994822a813645fc4b16d42b0f6c5c77f5) | `agent/test` = success (deterministic harness run), `agent/review` = success (Hermes) |
| Application code | written only by the OpenClaw Builder ([`655bdd6`](https://github.com/newsroom-ak/autonomous-newsroom/commit/655bdd68bfb318007e1c81848f02422abf64e98e), [`a24a227`](https://github.com/newsroom-ak/autonomous-newsroom/commit/a24a227ee95b8593e9089e2cded67c6bb6a70d80), [`0fa4f31`](https://github.com/newsroom-ak/autonomous-newsroom/commit/0fa4f31dcdb4c175dd173369cd1a6cb3bbf7dfc1)) |
| Recorded model runs | **12**: Dispatch 2, Builder 3, Tester 5, Reviewer 2 |
| **Reported tokens** | **3,026,133**, from the 11 runs that reported usage. **One Tester run (T3) did not report usage; its consumption is unknown, not zero.** |

## 2. Who did what

| Role | Runtime and model | In this ticket |
|---|---|---|
| **Hermes Dispatch** | Hermes, `nemotron-3-ultra` | Two assignments to the Builder (D1, D2). D2 contained an instruction that contradicted the ticket. |
| **OpenClaw Builder** | OpenClaw, `gpt-oss:120b` | Three attempts (B1 to B3); all application code in `app/newsroom/summarize.py` and its own unit tests |
| **OpenClaw Tester** | OpenClaw, `gpt-oss:20b` (T1), `gpt-oss:120b` (T2 to T5) | Five runs; wrote and rewrote the acceptance tests in `app/tests/acceptance/test_ticket_25.py` and fixtures in earlier acceptance files |
| **Hermes Reviewer** | Hermes, `nemotron-3-super` | Two reviews: request changes (R1), then approve (R2) |
| **Human-assisted** | the human engineer with Claude Code (not an agent) | Two test-only commits ([`4bb2657`](https://github.com/newsroom-ak/autonomous-newsroom/commit/4bb26573b5f57c7682a6bac09d03d30b113014fc), [`94d4c06`](https://github.com/newsroom-ak/autonomous-newsroom/commit/94d4c06994822a813645fc4b16d42b0f6c5c77f5)), the task and focus text for some agent runs, the disclosure comments |
| **Deterministic harness** | orchestrator code, no model | Ticket filing, escalation, committing the Tester's files, two verification runs |

## 3. Timeline

| UTC | Actor | Event |
|---|---|---|
| 18:27:42Z | HUMAN (via harness) | Issue [#25](https://github.com/newsroom-ak/autonomous-newsroom/issues/25) filed on behalf of `Ankita2525` |
| 18:29:54Z | AGENT Hermes Dispatch | D1: assigned #25 to the Builder (attempt 1/3) |
| 18:33:45Z | AGENT OpenClaw Builder | B1: pushed [`655bdd6`](https://github.com/newsroom-ak/autonomous-newsroom/commit/655bdd68bfb318007e1c81848f02422abf64e98e), PR [#26](https://github.com/newsroom-ak/autonomous-newsroom/pull/26) opened; `status:in-test` |
| 18:38:44Z | AGENT OpenClaw Tester | T1 (`gpt-oss:20b`): wrote no tests after tool errors; `agent/test` = error |
| 18:38:48Z | HARNESS | ticket labelled `status:needs-human` (no model called) |
| 18:43:27Z | AGENT OpenClaw Tester | T2 (`gpt-oss:120b`, human-authorized rerun): tests committed by the harness as [`540007b`](https://github.com/newsroom-ak/autonomous-newsroom/commit/540007bcfe823348d3ea851b088eebe660214a3e); 12 tests failing; `agent/test` = failure |
| 18:53:59Z | AGENT OpenClaw Tester (human-directed) | T3: rewrote its test file, [`3f63f0f`](https://github.com/newsroom-ak/autonomous-newsroom/commit/3f63f0f63ddb14a2f2874e8301ab4b557a3f3dba); run hit a gateway timeout, usage not reported; `agent/test` = failure |
| 19:00:48Z | AGENT OpenClaw Tester (human-directed) | T4: [`798da01`](https://github.com/newsroom-ak/autonomous-newsroom/commit/798da012e5e80b88ec76e051260e58a892ed62ca); 9 failing; `agent/test` = failure |
| 19:04:14Z | AGENT Hermes Dispatch | D2: re-assigned to the Builder (attempt 2/3); one instruction contradicted the ticket |
| 19:09:49Z | AGENT OpenClaw Builder (human-directed) | B2: [`a24a227`](https://github.com/newsroom-ak/autonomous-newsroom/commit/a24a227ee95b8593e9089e2cded67c6bb6a70d80); it added a plain-text compatibility path the ticket forbids |
| 19:12:53Z | AGENT OpenClaw Tester | T5: ran the existing suite, 1 failure (a blank summary stored as raw JSON); `agent/test` = failure |
| 19:20:26Z | AGENT OpenClaw Builder (human-directed) | B3: [`0fa4f31`](https://github.com/newsroom-ak/autonomous-newsroom/commit/0fa4f31dcdb4c175dd173369cd1a6cb3bbf7dfc1); removed the plain-text path, restored the strict JSON and `title[:120]` behaviour, added tests |
| 19:25:43Z | HUMAN-ASSISTED | [`4bb2657`](https://github.com/newsroom-ak/autonomous-newsroom/commit/4bb26573b5f57c7682a6bac09d03d30b113014fc): five outdated test fixtures corrected (no application code, no assertion changed) |
| 19:26:03Z | HARNESS | deterministic verification of the committed tests on `4bb2657`: pass (47 tests); no model called |
| 19:26:17Z | HUMAN | disclosure comment posted on PR #26 |
| 19:29:59Z | AGENT Hermes Reviewer | R1: CHANGES REQUESTED (three required regression tests missing); `agent/review` = failure |
| 19:34:52Z | HUMAN-ASSISTED | [`94d4c06`](https://github.com/newsroom-ak/autonomous-newsroom/commit/94d4c06994822a813645fc4b16d42b0f6c5c77f5): the three missing tests restored, append-only (no application code changed) |
| 19:35:12Z | HARNESS | deterministic verification on `94d4c06`: pass (50 tests); no model called; `agent/test` = success |
| 19:35:27Z | HUMAN | second disclosure comment posted on PR #26 |
| 19:40:27Z | AGENT Hermes Reviewer | R2: APPROVED; `agent/review` = success |
| 19:47:43Z | HUMAN | `Ankita2525` approved the PR on GitHub |
| 19:48:29Z | HUMAN | PR #26 merged into `main` as [`45504f4`](https://github.com/newsroom-ak/autonomous-newsroom/commit/45504f45808aceda523e3d5ddac923f5d878f544) (no ruleset bypass); Issue #25 closed at 19:48:30Z |

## 4. Model runs (12)

| Run | Finished | Agent (runtime) | Model | Outcome | Duration | Reported usage | Authorship | Session |
|---|---|---|---|---|---|---|---|---|
| D1 | 18:29:54Z | dispatch (hermes) | `nemotron-3-ultra` | assigned:builder | 38 s | 3,422 in / 522 out / **4,446** total (2 calls) | AGENT | Hermes `20261015_112914_5b3333` |
| B1 | 18:33:53Z | builder (openclaw) | `gpt-oss:120b` | handoff:tester | 65 s | 32,359 in / 7,975 out / **437,038** total (27 calls) | AGENT | OpenClaw `ticket-25-build-1` |
| T1 | 18:38:44Z | tester (openclaw) | `gpt-oss:20b` | tester-error | 79 s | 23,320 in / 800 out / **148,861** total (15 calls) | AGENT | OpenClaw `ticket-25-test-1` |
| T2 | 18:43:40Z | tester (openclaw) | `gpt-oss:120b` | fail | 65 s | 34,192 in / 12,468 out / **379,540** total (14 calls) | AGENT, human-authorized rerun with a model override | OpenClaw `ticket-25-test-1` (session reused) |
| T3 | 18:54:09Z | tester (openclaw) | not reported | fail | 272 s | **usage not reported** | AGENT, human-directed (human-written focus text) | OpenClaw `ticket-25-test-1-recheck` |
| T4 | 19:00:58Z | tester (openclaw) | `gpt-oss:120b` | fail | 123 s | 50,208 in / 3,604 out / **1,156,900** total (11 calls) | AGENT, human-directed (human-written focus text) | OpenClaw `ticket-25-test-1-recheck` (session reused) |
| D2 | 19:04:14Z | dispatch (hermes) | `nemotron-3-ultra` | assigned:builder | 61 s | 4,772 in / 1,530 out / **6,800** total (2 calls) | AGENT (its instruction contradicted the ticket) | Hermes `20261015_120311_0f08f9` |
| B2 | 19:09:53Z | builder (openclaw) | `gpt-oss:120b` | handoff:tester | 64 s | 33,609 in / 10,592 out / **255,769** total (17 calls) | AGENT, human-directed (human-written task text) | OpenClaw `ticket-25-build-2` |
| T5 | 19:12:53Z | tester (openclaw) | `gpt-oss:120b` | fail | 24 s | 22,906 in / 1,644 out / **242,742** total (17 calls) | AGENT | OpenClaw `ticket-25-test-2` |
| B3 | 19:20:29Z | builder (openclaw) | `gpt-oss:120b` | handoff:tester | 55 s | 14,689 in / 9,235 out / **344,644** total (22 calls) | AGENT, human-directed (human-written task text) | OpenClaw `ticket-25-build-3` |
| R1 | 19:29:59Z | reviewer (hermes) | `nemotron-3-super` | request_changes | 135 s | 14,362 in / 7,649 out / **22,523** total (2 calls) | AGENT | Hermes `20261015_122743_4aad37` |
| R2 | 19:40:27Z | reviewer (hermes) | `nemotron-3-super` | approve | 215 s | 15,541 in / 10,818 out / **26,870** total (2 calls) | AGENT | Hermes `20261015_123649_4a1d90` |

Run T3 hit a gateway timeout after about 272 s; the runtime returned no usage, so it appears in the ledger and on GitHub as 0 with no
model name. Treat it as **usage not reported**.

## 5. Token report

| Agent | Model | Runs reporting | Reported tokens |
|---|---|---:|---:|
| tester | `gpt-oss:120b` | 3 | 1,779,182 |
| builder | `gpt-oss:120b` | 3 | 1,037,451 |
| tester | `gpt-oss:20b` | 1 | 148,861 |
| reviewer | `nemotron-3-super` | 2 | 49,393 |
| dispatch | `nemotron-3-ultra` | 2 | 11,246 |
| | **Total reported** | **11** | **3,026,133** |

- One further Tester run (T3) did not report usage, so the true total is higher than 3,026,133 by an unknown amount.
- OpenClaw totals count the prompt again on each request, so they are much larger than input plus output; they are shown as reported.
- The ledger totals were cross-checked against the 18 event markers posted on GitHub for this ticket: 0 mismatches.

## 6. Commits in PR #26 and their gates

| Commit | Authorship | `agent/test` | `agent/review` |
|---|---|---|---|
| [`655bdd6`](https://github.com/newsroom-ak/autonomous-newsroom/commit/655bdd68bfb318007e1c81848f02422abf64e98e) | AGENT, Builder attempt 1 | error | none |
| [`540007b`](https://github.com/newsroom-ak/autonomous-newsroom/commit/540007bcfe823348d3ea851b088eebe660214a3e) | AGENT, Tester (committed by the harness) | failure | none |
| [`3f63f0f`](https://github.com/newsroom-ak/autonomous-newsroom/commit/3f63f0f63ddb14a2f2874e8301ab4b557a3f3dba) | AGENT, Tester, human-directed | failure | none |
| [`798da01`](https://github.com/newsroom-ak/autonomous-newsroom/commit/798da012e5e80b88ec76e051260e58a892ed62ca) | AGENT, Tester, human-directed | failure | none |
| [`a24a227`](https://github.com/newsroom-ak/autonomous-newsroom/commit/a24a227ee95b8593e9089e2cded67c6bb6a70d80) | AGENT, Builder attempt 2, human-directed | failure | none |
| [`0fa4f31`](https://github.com/newsroom-ak/autonomous-newsroom/commit/0fa4f31dcdb4c175dd173369cd1a6cb3bbf7dfc1) | AGENT, Builder attempt 3, human-directed | none (superseded before a gate ran) | none |
| [`4bb2657`](https://github.com/newsroom-ak/autonomous-newsroom/commit/4bb26573b5f57c7682a6bac09d03d30b113014fc) | **HUMAN-ASSISTED** (test fixtures only) | success (harness re-run) | failure |
| [`94d4c06`](https://github.com/newsroom-ak/autonomous-newsroom/commit/94d4c06994822a813645fc4b16d42b0f6c5c77f5) | **HUMAN-ASSISTED** (three tests restored) | success (harness re-run) | success |
| [`45504f4`](https://github.com/newsroom-ak/autonomous-newsroom/commit/45504f45808aceda523e3d5ddac923f5d878f544) | merge of PR #26 by the human owner | | |

Both human-assisted commits are authored by the human engineer, state in their messages that the OpenClaw Tester did not write them, and are
disclosed in comments on the PR. In the harness record, "attempt" counts Builder builds, so Tester and Reviewer runs repeat that number.

## 7. Human-written instructions that changed the agents' task

**Builder attempt 2 (B2).** The human replaced the ticket text for this run with the full original ticket plus this clarification:

> 1. The original Issue #25 acceptance criteria are authoritative.
> 2. The 160-character, whole-word truncation rule applies to successfully validated model-generated summaries.
> 3. When both model responses fail validation, preserve the existing fallback exactly: `summary = title[:120]`, `tag = "general"`, `summary_source = "fallback"`, and `summary_model = NULL`.
> 4. The previous Dispatch instruction to remove 120-character fallback logic was incorrect and must not be followed.
> 5. Update the outdated plain-text mocks in the Builder-owned `app/tests/test_summarize.py` to match the new structured JSON response contract.
> 6. The four remaining outdated mocks in Tester-owned acceptance files are not application defects. Do not modify those files or change application behavior to satisfy them.
> 7. Investigate multiline handling against the original requirements. Do not assume the implementation is defective solely because one test fails.
> 8. Preserve the two-model-call maximum, strict Pydantic validation, allowed tag taxonomy, and all other original acceptance criteria.

**Builder attempt 3 (B3).** The task file again carried the complete original ticket, plus these corrections (the text below is verbatim):

> ### Required corrections (only these two paths may change: `app/newsroom/summarize.py`, `app/tests/test_summarize.py`)
> 1. Remove the plain-text compatibility path introduced in attempt 2. A model response that is not valid JSON must be rejected.
> 2. Ensure Pydantic validation errors (and any other validation failure of a reply) are treated as failed model responses, never converted into successful summaries, and never stored as raw reply text.
> 3. Preserve the primary model → fallback model → literal `title[:120]` behaviour, with at most two model calls per article. When both replies are invalid or fail, store exactly `summary = title[:120]`, `tag = "general"`, `summary_source = "fallback"`, `summary_model = NULL`.
> 4. Preserve non-string tag normalization to `general` (a missing, non-string or unknown tag never rejects an otherwise valid reply).
> 5. Preserve the 160-character, whole-word truncation with the `…` suffix for valid model-generated summaries only.
> 6. Update all outdated plain-text mocks in the Builder-owned `app/tests/test_summarize.py` to valid JSON replies (for example `test_max_limit_and_idempotent`, which still returns plain text). Keep every existing assertion.
> 7. Do not modify any file under `app/tests/acceptance/` (Tester-owned); writes there are discarded. Do not change application behaviour to make an outdated test pass.
> 8. Multiline handling: Preserve strict JSON parsing. Do not rewrite malformed raw JSON by replacing unescaped newline characters before parsing. A raw newline inside a JSON string makes the reply invalid and must trigger the fallback model. A valid JSON response containing an escaped newline (`\n` represented as an escape sequence in the raw JSON) must be accepted, provided its summary passes validation, and the resulting summary must be normalized to one line. The existing Tester-owned multiline test appears to contain malformed JSON; do not weaken application validation to satisfy it.
> 9. In `app/tests/test_summarize.py`, add Builder-owned regression tests (valid JSON replies only in mocks) that prove: a plain-text reply is rejected and the fallback model is tried; a valid JSON object with a blank or meaningless summary is rejected and is not stored as a summary; and when both replies are invalid the stored summary is exactly `title[:120]` with tag `general`.
> 
> The earlier human clarification still applies: the original Issue #25 criteria are authoritative; the 160-character whole-word rule applies to validated model-generated summaries; the Dispatch instruction to remove 120-character fallback logic was incorrect; the four outdated mocks in Tester-owned acceptance files are not your defects.

**Tester runs T3 and T4.** The human supplied focus text asking the Tester to (T3) convert remaining plain-text mocks to JSON, fix a malformed multiline mock, remove state leaking into another test file, add coverage for non-string tags, invalid summaries, malformed JSON and whole-word truncation, and assert the literal `title[:120]` fallback; and (T4, narrower) add a missing import, fix the state leakage and convert four named mocks, without adding tests. Neither text told the agent how to write application code.

**Diagnostic run before any Tester run.** The human-assisted tooling ran the full suite on Builder attempt 1 ([`655bdd6`](https://github.com/newsroom-ak/autonomous-newsroom/commit/655bdd68bfb318007e1c81848f02422abf64e98e)): 29 passed, 9 failed. This was a diagnostic and not an agent gate.

## 8. What the gates and reviewers observably caught

- **Builder attempt 1** broke the existing title fallback contract (`title[:120]`) and left plain-text test mocks; caught by failing tests.
- **Tester run T1** wrote no tests after tool errors; the harness escalated to the human.
- **Tester runs T2 to T4** produced tests with defects (a missing import, state leaking into another test file, malformed JSON mocks), and one rewrite dropped three tests; the Hermes Reviewer later requested them back.
- **Dispatch assignment D2** told the Builder to remove the 120-character fallback, contradicting the ticket; the human clarified the ticket in the task text.
- **Builder attempt 2** added a plain-text compatibility path that the ticket forbids; an existing Tester test failed on it, and attempt 3 removed it.
- **Reviewer R1** requested changes (missing fenced-JSON, mis-cased-tag and prompt tests); **R2** approved after the human-assisted restoration.

## 9. Known limitations

- The summarizer prompt that the Builder wrote contains a contradictory sentence (it tells the model to include the article title and the word "JSON" in its response). It did not block the merge, but it should be fixed before this code is deployed; a follow-up ticket is planned.
- The deployed sites still run the earlier code; this ticket's merge did not redeploy anything.

## 10. Verify independently

```bash
gh pr view 26 --json mergeCommit,reviews,commits
gh api repos/newsroom-ak/autonomous-newsroom/commits/94d4c06994822a813645fc4b16d42b0f6c5c77f5/status
```
