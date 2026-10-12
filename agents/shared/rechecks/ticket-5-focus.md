Human review of your ticket #5 tests (change only what is listed; keep every other test):

1. `test_fallback_on_primary_402` asserts `summary_source == "fallback"` when the primary
   answers 402 and the FALLBACK MODEL succeeds. Per the ticket, `summary_source` is "model"
   whenever any model produced the summary, and `summary_model` names that model; "fallback"
   means no model produced it (the title-based fallback summary). Change that test to expect
   `summary_source == "model"` and `summary_model == <the fallback model name>`.

2. Add tests for these product-owner criteria (same mocking style, `httpx.post` patched):
   - primary returns 429, and separately raises `httpx.TimeoutException`: fallback model is used;
   - both models fail: `summary_source == "fallback"`, `summary_model` is NULL, tag "general",
     and the refresh run still succeeds;
   - `NEWSROOM_MAX_SUMMARIES=2` set with monkeypatch.setenv AFTER import: at most 2 model calls;
   - a later refresh with a changed title for the same `source_url` re-summarizes that article
     once; an unchanged title does not.

If the implementation does not meet these, the tests must fail; do not weaken them.
