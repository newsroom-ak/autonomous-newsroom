1. Parse actual RSS XML from a mocked HTTP response and verify database rows: patch the HTTP
   call (e.g. `httpx.get`) to return a small RSS 2.0 document with two items, let the real
   `feedparser` parsing run, call the refresh, and check the `articles` rows (source_url,
   title, published_at) and the `refresh_runs` row.
2. Malformed or incomplete feed entries: an item without a link, an item without a title, and
   a response that is not valid XML. The refresh must not crash; entries without a link are
   skipped; the run is recorded.
3. Network failures: the HTTP call raises (e.g. `httpx.ConnectError` / timeout) or returns
   HTTP 500. Calling the refresh function must not terminate the application (no
   `SystemExit` escaping the refresh function the scheduler will reuse), and the failure must
   be recorded in `refresh_runs` with status "error" and an error message.
