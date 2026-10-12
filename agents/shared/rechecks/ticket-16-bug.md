Production bug found during human-supervised live integration testing (2026-10-12). The merged
app (main `7d1ed3f`) was run against the real Google News RSS feed and real Ollama Cloud
(`gpt-oss:20b`). Two defects blocked a working newsroom; both passed all offline tests.

**Evidence**
- Refresh against `https://news.google.com/rss/search?q=AI+ML` fails: Google answers
  `302 Found` (to `...&hl=en-US&gl=US&ceid=US:en`); `feed.py` calls `httpx.get(url, timeout=10.0)`
  (redirects are not followed by default) then `raise_for_status()`, so the run is recorded as
  `error` and no articles are stored.
- With redirects followed, 4 of the first 10 summaries were stored as `summary_source="model"`
  with an **empty string**: `gpt-oss` is a reasoning model, and `max_tokens: 200` leaves no room
  for the answer after its hidden reasoning. The code accepts the empty reply, so the fallback
  model and the title fallback never run, the tag becomes "general", and the page shows a blank
  summary line.

### Acceptance criteria
- [ ] `feed.fetch_articles` follows HTTP redirects safely (a small maximum such as 5; `https`
      only) and sends a descriptive `User-Agent` header (e.g. `newsroom-bot/1.0`). A redirect
      response is no longer an error.
- [ ] A model reply that is empty, whitespace-only, or has fewer than 3 alphanumeric characters
      after cleaning is treated as an unusable response, not as a summary.
- [ ] When the primary model returns an unusable response, the fallback model is called; if
      the fallback returns a usable summary it is stored with `summary_source="model"` and
      `summary_model=<fallback model>`.
- [ ] If both models fail or return unusable responses, the clearly marked title-based fallback
      is stored (`summary_source="fallback"`, `summary_model` NULL, tag `general`), as before.
- [ ] No row is ever stored with `summary_source="model"` and an empty or meaningless summary.
- [ ] The completion token limit leaves room for reasoning models (raise `max_tokens` well above
      200, e.g. 800); the prompt still asks for one short sentence.
- [ ] Offline regression tests (no network; mock at the HTTP boundary, e.g. `httpx.MockTransport`
      or patching `httpx.get`/`httpx.post`) cover: a 302 followed by a 200 RSS body; the
      `User-Agent` header being sent; a redirect loop being cut off; an empty primary reply
      falling back; empty replies from both models falling back to the title.
- [ ] Existing tests that replace `httpx.get` with a function of a fixed signature
      (`lambda url, timeout=10.0: ...`) in `app/tests/acceptance/test_ticket_4.py` may be minimally
      updated by the tester so they still check their own criteria; do not weaken them.
- [ ] Scope: `app/newsroom/feed.py`, `app/newsroom/summarize.py` and their tests only. Do not
      redesign ingestion or summarization, change the schema, the page, the API or the scheduler,
      or add dependencies.

Depends on: none
