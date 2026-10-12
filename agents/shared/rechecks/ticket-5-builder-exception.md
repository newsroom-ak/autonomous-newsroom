Scoped repair. Edit ONLY `repo/app/tests/test_summarize.py`. Do not change any other file.
The production code is correct; two tests in that file have wrong expectations.

1. `test_fallback_used_on_primary_error`: when the primary raises HTTP 429 and the fallback
   model succeeds, the correct stored values are `summary_source == "model"` and
   `summary_model == os.getenv("SUMMARY_FALLBACK_MODEL", "nemotron-3-nano:30b")`.
   Replace only the two assertions on `source` and `model` (and fix the comment above them).

2. `test_max_limit_and_idempotent`: the cap applies per refresh. With 5 articles and a cap of
   3, the first `summarize_pending()` makes 3 calls, the second makes 2 more (the remaining
   articles), and a third makes none. Assert 3, then 5, then 5 calls in total.
   Set the cap with `monkeypatch.setenv("NEWSROOM_MAX_SUMMARIES", "3")` instead of assigning
   `os.environ[...]`, so the environment is restored after the test.

Read the file once, apply one patch, then reply with the two changes. Nothing else.
