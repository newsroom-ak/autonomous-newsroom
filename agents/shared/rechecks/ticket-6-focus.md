Human review of your ticket #6 tests (change only what is listed; keep every other test):

`test_articles_limit_and_ordering` replaces `newsroom.api.datetime` with a bare class that only
has `utcnow`. `api.py` also uses the `datetime` constructor (sort fallback) and
`datetime.fromisoformat` (date parsing), so the stub breaks the code under test
(`TypeError: dt() takes no arguments`). Make the clock deterministic without breaking those:
patch `newsroom.api.datetime` with a subclass of the real `datetime` that only overrides the
`utcnow` classmethod, or test `api._compute_updated_ago(last_refresh_iso, now_func)` directly
for `updated_ago` and keep the ordering assertions on the endpoint.

Keep the ordering, limit and fallback-to-fetched_at assertions. If the implementation does not
meet them, the test must fail; do not weaken it.
