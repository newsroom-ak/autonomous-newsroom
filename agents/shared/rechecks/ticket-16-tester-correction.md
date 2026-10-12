Human-authorized correction of YOUR tests. The production code is correct (verified with real
httpx objects); the remaining 8 failures are bad test doubles. Keep the intent of every test,
do not weaken any assertion, and do not edit non-test files.

Root cause: your fakes are not faithful `httpx.Response` objects (for example `url` is a plain
string, status codes are passed in the wrong argument position, and `fake_get` signatures have no
`headers` / `follow_redirects` parameters), and one stub chooses primary vs fallback from an
environment variable that is always the same.

Fix them by mocking at the HTTP boundary with a REAL httpx transport:

    def fake_get_factory(handler):
        def fake_get(url, **kwargs):
            with httpx.Client(transport=httpx.MockTransport(handler)) as c:
                return c.get(url, **kwargs)
        return fake_get
    monkeypatch.setattr(feed.httpx, "get", fake_get_factory(handler))   # handler(request) -> httpx.Response

- `test_redirect_followed_and_user_agent_sent`: handler returns `httpx.Response(302,
  headers={"Location": "/rss/search?q=x&hl=en-US"})` (a RELATIVE location) for the first URL and
  a 200 RSS body for the second; assert the article is parsed and every request's
  `request.headers["user-agent"]` is non-empty.
- `test_redirect_loop_exceeds_limit` / `test_non_https_redirect_raises`: real 302 responses with
  absolute `Location` headers (loop: always the same https URL; non-https: `http://...`); assert
  `httpx.HTTPError` is raised and the number of requests is at most 6.
- `test_summary_fallback_logic`: patch `summarize._call_model` (or `httpx.post` with a handler that
  switches on `json["model"]`, not on an env var). Cases: primary empty -> fallback model used
  (`summary_source == "model"`, `summary_model == fallback model`); both unusable -> title fallback
  (`"fallback"`, model NULL, tag "general").
- `app/tests/acceptance/test_ticket_4.py`: update every `fake_get` / `raise_connect` / `Resp500`
  doubles so they accept `**kwargs` (or use the factory above); keep all their assertions.

Do not edit anything else. If the product code does not meet a test, the test must fail.
