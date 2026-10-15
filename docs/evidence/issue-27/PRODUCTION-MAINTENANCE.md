# Production deployment and maintenance, 2026-10-15

**Everything on this page was done by the human engineer with Claude Code assistance. None of it was done by the Hermes or OpenClaw agents,
and none of it should be attributed to them.** No agent run and no agent-authored change is involved in the steps below; the only agent work
is the merged code of [PR #28](https://github.com/newsroom-ak/autonomous-newsroom/pull/28) that was deployed.

This is a public summary. The database backups, the rollback file and the operation records are kept privately and are not published. All times are UTC.

## 1. Deployment of `b62706a` to the AWS site (human-assisted)

Site: <https://3-13-197-253.sslip.io> (AWS EC2 `t3.small`, `us-east-2`). Terraform ignores application code (`ignore_changes`), so code is
deployed manually; this was not a Terraform change.

| Step | Result |
|---|---|
| Pre-deploy backup | SQLite online-backup copy taken, verified (259 articles, `integrity_check` ok) and copied to the engineer's Mac |
| What changed | Fast-forward from `f3349a0` to [`b62706a`](https://github.com/newsroom-ak/autonomous-newsroom/commit/b62706aa7dcd25fc8885ce350607c6d2eb02ddaf). The only runtime change was `app/newsroom/summarize.py` (it now imports `pydantic`, already installed on the VM). |
| Checks before restart | Import check of the new code passed |
| Restart | One `systemctl restart` at 20:52:33Z; the site answered again after 27 s; no further restarts |
| Existing data | The 259 existing articles and the earlier refresh runs were byte-identical afterwards; `integrity_check` ok |
| Startup refresh (run 32, 20:52:34Z to 20:52:58Z) | 100 items fetched, **4 new articles**, exactly **4 model calls**, all `gpt-oss:20b`, all with tags from the 11-tag taxonomy |

No existing article was reprocessed during the deployment.

## 2. The four articles summarized automatically after the deployment

These are the four new articles from the startup refresh above, summarized by the deployed pipeline without human input.

| Article | Tag | Grounding review (headline only) |
|---|---|---|
| 3101 | `business` | Supported |
| 3103 | `products` | Partially supported (states outcomes the headline does not) |
| 3109 | `models` | Partially supported (adds "LLM" and an aim not in the headline) |
| 3091 | `safety` | Partially supported (adds details not in the headline) |

All four tags are reasonable. The summaries were left as the pipeline produced them.

## 3. The six tag-only corrections (human-assisted production data maintenance)

Six older articles on the homepage still carried legacy, first-word tags from before PR #26 (for example `meta`, `the`, `google`, `explores`,
`ai`). The engineer reprocessed them in two phases.

**Phase 1, candidates only (no database write).** The deployed code's own `_summarize_with_fallback` was run once per article against a
read-only connection, with a hard cap of 12 model calls. It used 7 calls and 5,599 provider-reported tokens and wrote nothing. The
candidate *summaries* were deliberately **not** applied, because the grounding review (section 4) found embellishments the headlines do not support.

**Phase 2, tags only.** One atomic transaction (`BEGIN IMMEDIATE`, with a compare-and-swap on the old tag) changed exactly six rows and only
the `topic_tag` column, after a fresh verified backup and a rollback file were taken. Summaries, titles, sources, models and everything else
were untouched; the other 257 article rows and 32 refresh runs were verified identical; `integrity_check` ok; 263 articles; no restart. All
ten homepage tags are now in the taxonomy.

| Article | Old tag | New tag | Where the new tag came from |
|---|---|---|---|
| 2986 | `meta` | `products` | model candidate (Phase 1) |
| 2988 | `the` | `hardware` | model candidate (Phase 1) |
| 3005 | `google` | `tools` | model candidate (Phase 1) |
| 2886 | `explores` | `business` | model candidate (Phase 1) |
| 2998 | `google` | `tools` | **analyst-selected**: both models hit the 800-token cap and the pipeline fell back to the title, so there was no model tag |
| 2898 | `ai` | `general` | **analyst-selected**: the model proposed `products`, which the truncated headline does not support |

Four tags are therefore model output chosen by the human; two were chosen by the human directly. The older rows not listed here (242 of the
original 259 had a legacy tag) keep their legacy tags; only rows the summarizer processes from now on use the taxonomy.

## 4. Limitation: valid JSON is not grounded in the headline

Strict JSON and Pydantic validation (Issue #25) make the *format* of the model's output consistent: one line, at most 160 characters, a tag from
a fixed list. It does **not** guarantee that a summary contains only claims supported by the headline. The summarizer sends the model only the
article title (the RSS description is not used), so the model sometimes adds plausible details that the title does not state.

The human-assisted review of nine summaries produced by the new pipeline (the five candidates from Phase 1 that were not title fallbacks, and
the four live articles above) found: **1 supported, 5 partially supported, 3 unsupported.** This is a **small, non-representative sample**
judged by a person reading the headline, not a measured error rate, and it should not be generalized. It is enough to show that the failure
mode exists. A suggested follow-up ticket would instruct the model to use only the information in the title and/or feed it the RSS description.

## 5. What is not published

Database backups and the rollback file, the operation records, agent transcripts, environment and token files, and private evidence. No
secret, key or credential is part of this summary.
