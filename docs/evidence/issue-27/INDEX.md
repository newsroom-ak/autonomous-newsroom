# Issue #27: evidence index

**Ticket:** [#27 Remove contradictory instructions from the summarizer prompt](https://github.com/newsroom-ak/autonomous-newsroom/issues/27)
**Pull request:** [#28](https://github.com/newsroom-ak/autonomous-newsroom/pull/28), merged into `main` as [`b62706a`](https://github.com/newsroom-ak/autonomous-newsroom/commit/b62706aa7dcd25fc8885ce350607c6d2eb02ddaf); final head [`b1bbe53`](https://github.com/newsroom-ak/autonomous-newsroom/commit/b1bbe532f75de4918c011faf2dbf2105c28269d1)

A curated summary of a small follow-up ticket delivered by the Hermes and OpenClaw agent team, with the failure, the recovery and
the human involvement stated explicitly. All times are UTC on 2026-10-15 and come from the orchestrator ledger
([`ledger-ticket-27.jsonl`](ledger-ticket-27.jsonl), the verbatim 10 events for this ticket) and from GitHub. Every token figure is
**as reported by the agent runtimes**, never estimated. The ticket follows [Issue #25](https://github.com/newsroom-ak/autonomous-newsroom/issues/25)
([index](https://github.com/newsroom-ak/autonomous-newsroom/blob/evidence/issue-25/docs/evidence/issue-25/INDEX.md)), whose summarizer prompt contained two
sentences written for the *test* that had been copied into the prompt as instructions for the model (the prompt told the model to include
the article title and the word "JSON" in its reply).

**Withheld on purpose:** raw agent session transcripts (they include stored model reasoning and tool traces), the full task files, and
anything about the production database beyond the summary in [PRODUCTION-MAINTENANCE.md](PRODUCTION-MAINTENANCE.md).

## 1. Result

| | |
|---|---|
| Issue #27 | filed through the harness on behalf of `Ankita2525` at 20:25:58Z; closed as completed 20:47:42Z |
| PR #28 | approved by `Ankita2525` 20:47:18Z on `b1bbe53`; merged 20:47:41Z, merge commit [`b62706a`](https://github.com/newsroom-ak/autonomous-newsroom/commit/b62706aa7dcd25fc8885ce350607c6d2eb02ddaf); **no ruleset bypass** |
| Final gates on `b1bbe53` | `agent/test` = success (deterministic harness re-run, no model), `agent/review` = success (Hermes) |
| Application code | written only by the OpenClaw Builder (`e603085`: +7/-2 lines in `app/newsroom/summarize.py`, plus its own tests in `app/tests/test_summarize_prompt.py`) |
| Recorded model runs | **6**: Dispatch 1, Builder 1, Tester 2, Reviewer 2 |
| **Reported tokens** | **1,100,777**, all six runs reported usage |

## 2. Who did what

| Role | Runtime and model | In this ticket |
|---|---|---|
| **Hermes Dispatch** | Hermes, `nemotron-3-ultra` | One assignment to the Builder |
| **OpenClaw Builder** | OpenClaw, `gpt-oss:120b` | One attempt: removed the two contradictory sentences, added "Return a single valid JSON object.", wrote prompt unit tests |
| **OpenClaw Tester** | OpenClaw, `gpt-oss:120b` | Two runs. Run 1 wrote an invalid file; run 2 (a focused rerun) wrote the valid acceptance tests. |
| **Hermes Reviewer** | Hermes, `nemotron-3-super` | Two approvals (on `d63ca81`, then on `b1bbe53`) |
| **Human-assisted** | the human engineer with Claude Code (not an agent) | The ticket text, one short Tester focus note, **one test-only commit (`b1bbe53`)**, two disclosure comments |
| **Deterministic harness** | orchestrator code, no model | Ticket filing, committing the Tester's file, the verification re-run on `b1bbe53` |

## 3. Timeline

| UTC | Actor | Event |
|---|---|---|
| 20:25:58Z | HUMAN (via harness) | Issue #27 filed |
| 20:27:29Z | AGENT Hermes Dispatch | assigned #27 to the Builder (attempt 1/3) |
| 20:29:16Z | AGENT OpenClaw Builder | pushed [`e603085`](https://github.com/newsroom-ak/autonomous-newsroom/commit/e603085735d4fe1ba4f231025cd3d09f95c8a013); PR #28 opened |
| 20:32:15Z | AGENT OpenClaw Tester | run 1 (`gpt-oss:120b`): file committed by the harness as [`31e6b9d`](https://github.com/newsroom-ak/autonomous-newsroom/commit/31e6b9dc1df6ae44d9277a12bdb7b204aedc427f); it contained literal backslash-n sequences instead of line breaks, so pytest failed at collection; `agent/test` = failure |
| 20:35:22Z | AGENT OpenClaw Tester (human-directed) | run 2, one focused rerun: [`d63ca81`](https://github.com/newsroom-ak/autonomous-newsroom/commit/d63ca81851acbf3db5c9addd9ebd22673f5dc354); valid file, 57 tests passed; `agent/test` = success |
| 20:36:16Z | HUMAN | disclosure comment on PR #28 (the exact focus note, verbatim) |
| 20:39:07Z | AGENT Hermes Reviewer | review 1: APPROVED on `d63ca81`; `agent/review` = success |
| 20:43:21Z | HUMAN-ASSISTED | [`b1bbe53`](https://github.com/newsroom-ak/autonomous-newsroom/commit/b1bbe532f75de4918c011faf2dbf2105c28269d1): two regression tests appended (27 lines), see section 6 |
| 20:43:42Z | HUMAN | second disclosure comment on PR #28 |
| 20:44:58Z | HARNESS | deterministic verification on `b1bbe53`: pass (59 tests), no model called; `agent/test` = success |
| 20:46:18Z | AGENT Hermes Reviewer | review 2: APPROVED on `b1bbe53`; `agent/review` = success |
| 20:47:18Z | HUMAN | `Ankita2525` approved the PR |
| 20:47:41Z | HUMAN | PR #28 merged as `b62706a` (no ruleset bypass); Issue #27 closed 20:47:42Z |

## 4. Model runs (6)

| Run | Finished | Agent (runtime) | Model | Outcome | Reported usage | Authorship |
|---|---|---|---|---|---|---|
| D1 | 20:27:29Z | dispatch (hermes) | `nemotron-3-ultra` | assigned: builder | 3,358 in / 539 out / **4,377** total (2 calls) | AGENT |
| B1 | 20:29:24Z | builder (openclaw) | `gpt-oss:120b` | handoff: tester | 20,697 in / 6,809 out / **314,114** total (23 calls) | AGENT |
| T1 | 20:32:27Z | tester (openclaw) | `gpt-oss:120b` | fail (pytest collection error) | 31,337 in / 7,004 out / **610,405** total (35 calls) | AGENT |
| T2 | 20:35:35Z | tester (openclaw) | `gpt-oss:120b` | pass | 9,213 in / 4,543 out / **154,620** total (13 calls) | AGENT, human-directed (human-written focus note) |
| R1 | 20:39:07Z | reviewer (hermes) | `nemotron-3-super` | approve | 6,735 in / 1,742 out / **8,967** total (2 calls) | AGENT |
| R2 | 20:46:18Z | reviewer (hermes) | `nemotron-3-super` | approve | 7,070 in / 737 out / **8,294** total (2 calls) | AGENT |

## 5. Token report

| Agent | Model | Runs | Reported tokens |
|---|---|---:|---:|
| tester | `gpt-oss:120b` | 2 | 765,025 |
| builder | `gpt-oss:120b` | 1 | 314,114 |
| reviewer | `nemotron-3-super` | 2 | 17,261 |
| dispatch | `nemotron-3-ultra` | 1 | 4,377 |
| | **Total reported** | **6** | **1,100,777** |

OpenClaw totals count the prompt again on each request, so they are far larger than input plus output; they are shown as reported.
No run is missing a usage record.

## 6. Commits in PR #28 and their gates

| Commit | Authorship | `agent/test` | `agent/review` |
|---|---|---|---|
| `e603085` | AGENT, Builder attempt 1 | none | none |
| `31e6b9d` | AGENT, Tester run 1 (committed by the harness) | failure | none |
| `d63ca81` | AGENT, Tester run 2, human-directed | success | success |
| `b1bbe53` | **HUMAN-ASSISTED** (tests only) | success (harness re-run) | success |
| `b62706a` | merge of PR #28 by the human owner | | |

**What `b1bbe53` is.** After the Reviewer approved `d63ca81`, independent mutation checks, run by the human engineer with Claude Code in a
disposable worktree, showed that the existing prompt tests would not catch (a) a reworded contradictory instruction ("Also include the word
JSON in your reply.") or (b) the article title appearing twice in the prompt. Both are covered by the ticket's acceptance criteria. The
human therefore authorized two small tests, appended to the Tester's acceptance file with an in-file provenance comment:
`test_prompt_has_no_prohibited_instruction_patterns` (the prompt must not match `in your prompt`, `include the word` or `in the response`,
case-insensitively) and `test_unique_title_appears_exactly_once_in_prompt` (a unique title appears exactly once in the whole prompt).
The commit only appends 27 lines; it changes no application code, no Builder-owned test and no existing assertion. **It is not OpenClaw
Tester output**, its message and a PR comment say so, and Hermes then reviewed it again. Before committing, each mutation was missed by the
existing prompt tests and caught by the matching new test, and the full offline suite passed.

**Observation about the Reviewer.** The Hermes Reviewer approved `d63ca81` without noticing these gaps. The harness shows it only the issue,
the diff and the test output, so it cannot see coverage gaps. The strengthening came from a human-led check, not from the agents.

## 7. Human-written instructions that changed the agents' task

**Tester focus note (run 2), verbatim:**

> The previous Tester acceptance file `app/tests/acceptance/test_ticket_27.py` failed Python collection because it contains literal backslash-n sequences instead of actual line breaks. Rewrite the file as syntactically valid, multiline Python source. Preserve independent coverage of Issue #27's acceptance criteria. Do not modify application code, Builder-owned tests, or existing acceptance tests. Verify Python syntax before completing, if the available tools permit it.

The note contains no test content and no application-code instructions. The file in `d63ca81` was written by the OpenClaw Tester.

The Builder and the Dispatch agent ran with the ticket text as filed; no task-file override was used for them.

## 8. What the gates and humans observably caught

- **Tester run 1** wrote an invalid-Python file; `agent/test` failed at collection and one focused rerun recovered it. (The same defect, literal `\n` sequences, had occurred on ticket #7.)
- **Hermes Reviewer** approved while two criteria were weakly covered (section 6); a human-led check found and closed them with test-only additions.

## 9. Known limitations

- The Reviewer cannot see coverage gaps (it receives only the issue, diff and test output). Giving it mutation or coverage evidence is an improvement listed in `DELIVERY.md`.
- This ticket only corrects the prompt. It does not make summaries factually grounded in the headline; see the limitation described in [PRODUCTION-MAINTENANCE.md](PRODUCTION-MAINTENANCE.md).

## 10. Verify independently

```bash
gh pr view 28 --json mergeCommit,reviews,commits
gh api repos/newsroom-ak/autonomous-newsroom/commits/b1bbe532f75de4918c011faf2dbf2105c28269d1/status
gh api repos/newsroom-ak/autonomous-newsroom/issues/28/comments
```
