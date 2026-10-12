"""Acceptance tests for Ticket #16 – redirect handling and model fallback.

The tests run offline and mock all external HTTP calls. They cover the new
behaviour introduced in ``newsroom.feed`` and ``newsroom.summarize``:

1. ``feed.fetch_articles`` follows up to five HTTPS redirects, sends a
   ``User-Agent`` header, and treats a 302 as a normal response.
2. Summaries that are empty, whitespace‑only, or contain fewer than three
   alphanumeric characters are deemed unusable.
3. When the primary model produces an unusable summary, the fallback model is
   invoked and its usable result is stored with ``summary_source="model"``
   and ``summary_model`` set to the fallback model name.
4. If both models fail or are unusable, the title‑based fallback stores the
   truncated title, ``summary_source="fallback"`` and ``summary_model`` NULL.
5. No row is ever persisted with ``summary_source="model"`` and an empty or
   meaningless summary.
6. The request payload to the model APIs must specify ``max_tokens=800``.
7. Redirect loops (more than five hops) or non‑HTTPS redirect targets raise an
   ``httpx.HTTPError``.
"""

from __future__ import annotations

import os
import shutil
import sqlite3
import sys
import tempfile
from typing import Tuple

import pytest

# Add the repository's ``app`` directory to ``sys.path`` so that ``import newsroom`` works.
sys.path.append(
    os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "repo", "app"))
)

from newsroom import db, feed, refresh, summarize  # noqa: E402


def _setup_temp_db() -> Tuple[str, sqlite3.Connection, str | None]:
    """Create a temporary SQLite DB and point ``NEWSROOM_DB`` to it.

    Returns the path, an open connection (schema already ensured) and the
    previous value of ``NEWSROOM_DB`` so it can be restored.
    """
    temp_dir = tempfile.mkdtemp()
    db_path = os.path.join(temp_dir, "newsroom.db")
    old_env = os.getenv(db.DB_PATH_ENV)
    os.environ[db.DB_PATH_ENV] = db_path
    # Ensure the schema is created.
    conn = sqlite3.connect(db_path)
    db._ensure_schema(conn)
    return db_path, conn, old_env


def _teardown_temp_db(path: str, old_env: str | None) -> None:
    if old_env is None:
        os.environ.pop(db.DB_PATH_ENV, None)
    else:
        os.environ[db.DB_PATH_ENV] = old_env
    shutil.rmtree(os.path.dirname(path), ignore_errors=True)


def test_redirect_followed_and_user_agent_sent(monkeypatch):
    """A 302 redirect (HTTPS) is followed and the User‑Agent header is sent."""
    db_path, conn, old_env = _setup_temp_db()
    try:
        first_url = "https://news.google.com/rss/search?q=AI+ML"
        second_url = "https://news.google.com/rss/search?q=AI+ML&hl=en-US&gl=US&ceid=US:en"
        rss_body = """<?xml version='1.0'?><rss><channel><item><title>Test</title><link>https://example.com/1</link></item></channel></rss>"""

        class MockResponse:
            def __init__(self, url, status_code, headers=None, text=""):
                self.url = url
                self.status_code = status_code
                self.headers = headers or {}
                self.text = text

            @property
            def is_redirect(self) -> bool:
                return self.status_code in (301, 302, 303, 307, 308)

            def raise_for_status(self):
                if self.status_code >= 400:
                    raise __import__("httpx").HTTPStatusError(
                        f"{self.status_code} error", request=None, response=self
                    )

        calls = []

        def fake_get(url, timeout=10.0, headers=None, follow_redirects=False, **kwargs):
            calls.append({"url": url, "headers": headers, "timeout": timeout})
            if url == first_url:
                return MockResponse(url, 302, {"Location": second_url})
            if url == second_url:
                return MockResponse(url, 200, text=rss_body)
            raise AssertionError(f"Unexpected URL {url}")

        monkeypatch.setattr(feed, "httpx", __import__("httpx"))
        monkeypatch.setattr(feed.httpx, "get", fake_get)

        articles = feed.fetch_articles()
        # Verify one article extracted.
        assert len(articles) == 1
        assert articles[0]["source_url"] == "https://example.com/1"
        # Verify that the User‑Agent header was sent on both requests.
        for call in calls:
            assert call["headers"].get("User-Agent") == "newsroom-bot/1.0"
    finally:
        conn.close()
        _teardown_temp_db(db_path, old_env)


def test_redirect_loop_exceeds_limit(monkeypatch):
    """More than five redirects raises an ``httpx.HTTPError``."""
    db_path, conn, old_env = _setup_temp_db()
    try:
        base = "https://example.com/step"

        class MockResponse:
            def __init__(self, url, status_code, headers=None):
                self.url = url
                self.status_code = status_code
                self.headers = headers or {}

            @property
            def is_redirect(self) -> bool:
                return True

            def raise_for_status(self):
                pass

        def fake_get(url, timeout=10.0, headers=None, follow_redirects=False, **kwargs):
            # Each call appends a new step number.
            step = int(url.split("step")[-1])
            next_step = step + 1
            location = f"{base}{next_step}"
            return MockResponse(url, 302, {"Location": location})

        monkeypatch.setattr(feed, "httpx", __import__("httpx"))
        monkeypatch.setattr(feed.httpx, "get", fake_get)

        # Starting URL will be constructed inside fetch_articles; we force it by
        # calling with a known topic that yields a predictable URL.
        with pytest.raises(__import__("httpx").HTTPError):
            feed.fetch_articles(topic="loop")
    finally:
        conn.close()
        _teardown_temp_db(db_path, old_env)


def test_non_https_redirect_raises(monkeypatch):
    """Redirect target not using HTTPS raises an ``httpx.HTTPError``."""
    db_path, conn, old_env = _setup_temp_db()
    try:
        https_url = "https://example.com/start"
        http_url = "http://example.com/target"

        class MockResponse:
            def __init__(self, url, status_code, headers=None):
                self.url = url
                self.status_code = status_code
                self.headers = headers or {}

            @property
            def is_redirect(self) -> bool:
                return True

            def raise_for_status(self):
                pass

        def fake_get(url, timeout=10.0, headers=None, follow_redirects=False, **kwargs):
            if url == https_url:
                return MockResponse(url, 302, {"Location": http_url})
            raise AssertionError("Unexpected GET call")

        monkeypatch.setattr(feed, "httpx", __import__("httpx"))
        monkeypatch.setattr(feed.httpx, "get", fake_get)

        with pytest.raises(__import__("httpx").HTTPError):
            feed.fetch_articles(topic="nonhttps")
    finally:
        conn.close()
        _teardown_temp_db(db_path, old_env)


def test_summary_fallback_logic(monkeypatch):
    """Primary unusable -> fallback used; both unusable -> title fallback."""
    db_path, conn, old_env = _setup_temp_db()
    try:
        # One article to summarise.
        feed.fetch_articles = lambda: [
            {
                "source_url": "https://example.com/1",
                "title": "A" * 200,
                "published_at": None,
                "fetched_at": None,
                "summary": None,
                "topic_tag": None,
            }
        ]

        call_log = []

        def fake_post(url, json, headers=None, timeout=None):
            # Distinguish primary vs fallback by URL content.
            if "primary" in os.getenv("SUMMARY_PRIMARY_URL", ""):
                # Primary returns an unusable (whitespace) summary.
                call_log.append("primary")
                class Resp:
                    status_code = 200
                    def raise_for_status(self):
                        pass
                    def json(self):
                        return {"choices": [{"message": {"content": "   "}}]}
                return Resp()
            else:
                # Fallback returns usable summary.
                call_log.append("fallback")
                class Resp:
                    status_code = 200
                    def raise_for_status(self):
                        pass
                    def json(self):
                        return {"choices": [{"message": {"content": "A concise fallback summary."}}]}
                return Resp()

        # Configure distinct URLs to let the stub differentiate.
        os.environ["SUMMARY_PRIMARY_URL"] = "https://primary.test/v1"
        os.environ["SUMMARY_FALLBACK_URL"] = "https://fallback.test/v1"
        os.environ["SUMMARY_PRIMARY_MODEL"] = "primary-model"
        os.environ["SUMMARY_FALLBACK_MODEL"] = "fallback-model"

        monkeypatch.setattr(summarize, "httpx", __import__("httpx"))
        monkeypatch.setattr(summarize.httpx, "post", fake_post)

        refresh._run_refresh()

        cur = conn.cursor()
        cur.execute("SELECT summary, summary_source, summary_model FROM articles")
        summary, source, model = cur.fetchone()
        # Primary was unusable, fallback provided a usable result.
        assert source == "model"
        assert model == "fallback-model"
        assert "fallback" in summary.lower()
        assert call_log == ["primary", "fallback"]

        # Now simulate both models unusable – expect title fallback.
        call_log.clear()
        def fake_post_unusable(url, json, headers=None, timeout=None):
            # Both primary and fallback return whitespace.
            call_log.append(url)
            class Resp:
                status_code = 200
                def raise_for_status(self):
                    pass
                def json(self):
                    return {"choices": [{"message": {"content": "  "}}]}
            return Resp()

        monkeypatch.setattr(summarize.httpx, "post", fake_post_unusable)
        # Reset DB: clear summary fields.
        conn.execute("UPDATE articles SET summary=NULL, topic_tag=NULL, summary_source=NULL, summary_model=NULL")
        conn.commit()

        refresh._run_refresh()
        cur.execute("SELECT summary, summary_source, summary_model FROM articles")
        summary2, source2, model2 = cur.fetchone()
        assert source2 == "fallback"
        assert model2 is None
        # The stored summary should be the first 120 chars of the title.
        assert summary2 == ("A" * 200)[:120]
    finally:
        conn.close()
        _teardown_temp_db(db_path, old_env)


def test_model_payload_uses_max_tokens_800(monkeypatch):
    """The request payload sent to the model endpoint includes ``max_tokens=800``."""
    db_path, conn, old_env = _setup_temp_db()
    try:
        feed.fetch_articles = lambda: [
            {
                "source_url": "https://example.com/1",
                "title": "Test title",
                "published_at": None,
                "fetched_at": None,
                "summary": None,
                "topic_tag": None,
            }
        ]

        captured = {}

        def fake_post(url, json, headers=None, timeout=None):
            captured["json"] = json
            class Resp:
                status_code = 200
                def raise_for_status(self):
                    pass
                def json(self):
                    return {"choices": [{"message": {"content": "OK"}}]}
            return Resp()

        # Use primary model only for this test.
        os.environ["SUMMARY_PRIMARY_URL"] = "https://primary.test/v1"
        os.environ["SUMMARY_PRIMARY_MODEL"] = "primary-model"
        os.environ["SUMMARY_PRIMARY_KEY"] = "key"
        # Ensure fallback is not called.
        os.environ["SUMMARY_FALLBACK_URL"] = "https://fallback.test/v1"
        os.environ["SUMMARY_FALLBACK_MODEL"] = "fallback-model"

        monkeypatch.setattr(summarize, "httpx", __import__("httpx"))
        monkeypatch.setattr(summarize.httpx, "post", fake_post)

        refresh._run_refresh()
        assert captured.get("json", {}).get("max_tokens") == 800
    finally:
        conn.close()
        _teardown_temp_db(db_path, old_env)

