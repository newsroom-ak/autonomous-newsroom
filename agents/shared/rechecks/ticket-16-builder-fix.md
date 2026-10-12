Scoped repair of two product defects found by the tester's tests. Edit ONLY
`repo/app/newsroom/feed.py` and `repo/app/newsroom/summarize.py`. Do not change anything else.

1. feed.py: `URL(location, base=resp.url)` raises `TypeError: 'base' is an invalid keyword
   argument for URL()` (httpx.URL has no `base` argument), so a real redirect crashes.
   Resolve the Location header against the current URL correctly, e.g.
   `str(resp.url.join(location))` or `urllib.parse.urljoin(str(resp.url), location)`.
   Keep the limit of 5 redirects, the https-only rule, the User-Agent header and the error
   behaviour; do not rewrite the loop.

2. summarize.py `_summarize_with_fallback`: when the FALLBACK model returns an unusable reply
   without raising (empty / whitespace / fewer than 3 alphanumerics), the function falls off the
   end of the `try` and returns None, so the caller drops the article (it is retried forever and
   never gets the title fallback). When both models fail or both replies are unusable it must
   return the title-based fallback: `(title[:120], "general", "fallback", None)`. Restructure so
   every path returns a 4-tuple (e.g. a final `return` after the try blocks). Also let any
   exception from the primary call (not only httpx errors) fall through to the fallback model.

Read each file once, apply one patch per file, then reply. Nothing else.
