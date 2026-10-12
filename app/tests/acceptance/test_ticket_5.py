"""Acceptance tests for ticket #5 – article summarization with fallback.

The tests exercise the public API (refresh._run_refresh) and verify that:

* Summaries and topic tags are populated for new articles.
* The primary model endpoint, model name and API key are taken from the
  environment variables.
* A 402/429 response (or any other request error) from the primary model
  triggers a fallback model call.
* When both model calls fail, the summary falls back to a truncated title and
  the tag is ``"general"``.
* Summarisation is idempotent – re‑running refresh does not modify already
  summarised rows.

All external HTTP calls are mocked; the tests run completely offline.
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
    os.path.abspath(os.path.join(os.path.dirname(__file__), "../..", "repo", "app"))
)

from newsroom import db, refresh, summarize, feed  # noqa: E402


def _setup_temp_db() -> Tuple[str, sqlite3.Connection, str | None]:
    """Create a temporary SQLite DB and point the env var to it.

    Returns the path, an open connection (schema is already ensured) and the
    previous value of ``NEWSROOM_DB`` so it can be restored.
    """
    temp_dir = tempfile.mkdtemp()
    db_path = os.path.join(temp_dir, "newsroom.db")
    old_env = os.getenv(db.DB_PATH_ENV)
    os.environ[db.DB_PATH_ENV] = db_path
    # Ensure schema creation.
    conn = sqlite3.connect(db_path)
    db._ensure_schema(conn)
    return db_path, conn, old_env


def _teardown_temp_db(path: str, old_env: str | None):
    if old_env is None:
        os.environ.pop(db.DB_PATH_ENV, None)
    else:
        os.environ[db.DB_PATH_ENV] = old_env
    shutil.rmtree(os.path.dirname(path), ignore_errors=True)


def _mock_feed_single(title: str, url: str = "https://example.com/article"):
    """Monkey‑patch ``feed.fetch_articles`` to return a single article dict.
    The dict mirrors the structure expected by ``refresh._run_refresh``.
    """

    article = {
        "source_url": url,
        "title": title,
        "published_at": None,
        "fetched_at": None,
        "summary": None,
        "topic_tag": None,
    }
    feed.fetch_articles = lambda: [article]


def test_primary_model_called_and_summary_populated(monkeypatch):
    """Refresh should call the primary model and store its summary/tag.
    """
    db_path, conn, old_env = _setup_temp_db()
    try:
        _mock_feed_single("Primary model test")

        # Capture the HTTP request made by ``summarize._call_model``.
        captured: dict = {}

        def fake_post(url, json, headers=None, timeout=None):  # pragma: no cover
            captured["url"] = url
            captured["json"] = json
            captured["headers"] = headers
            class Resp:
                status_code = 200

                def raise_for_status(self):
                    pass

                def json(self):
                    return {"choices": [{"message": {"content": "A concise summary."}}]}

            return Resp()

        monkeypatch.setattr(summarize, "httpx", __import__("httpx"))
        monkeypatch.setattr(summarize.httpx, "post", fake_post)

        # Ensure env vars have known values.
        os.environ["SUMMARY_PRIMARY_URL"] = "https://primary.test/v1"
        os.environ["SUMMARY_PRIMARY_MODEL"] = "test-primary-model"
        os.environ["SUMMARY_PRIMARY_KEY"] = "primary-key"

        refresh._run_refresh()

        # Verify HTTP request target.
        assert captured["url"].endswith("/chat/completions")
        assert captured["json"]["model"] == "test-primary-model"
        assert captured["headers"].get("Authorization") == "Bearer primary-key"

        # Verify DB row has summary and tag.
        cur = conn.cursor()
        cur.execute("SELECT summary, topic_tag, summary_source, summary_model FROM articles")
        summary, tag, source, model = cur.fetchone()
        assert summary == "A concise summary."
        assert isinstance(tag, str) and tag  # non‑empty
        assert source == "model"
        assert model == "test-primary-model"
    finally:
        conn.close()
        _teardown_temp_db(db_path, old_env)


def test_fallback_on_primary_402(monkeypatch):
    """When primary returns 402/429, fallback model is used.
    """
    db_path, conn, old_env = _setup_temp_db()
    try:
        _mock_feed_single("Fallback test")

        call_sequence = []

        def fake_post(url, json, headers=None, timeout=None):
            # Decide based on URL to simulate primary vs fallback.
            if "primary" in url:
                # Simulate 429 error.
                class Resp:
                    status_code = 429

                    def raise_for_status(self):
                        raise __import__("httpx").HTTPStatusError(
                            "429 Too Many Requests", request=None, response=self
                        )

                raise Resp().raise_for_status()
            else:
                call_sequence.append("fallback")
                class Resp:
                    status_code = 200

                    def raise_for_status(self):
                        pass

                    def json(self):
                        return {"choices": [{"message": {"content": "Fallback summary."}}]}

                return Resp()

        # Patch env to have distinguishable URLs.
        os.environ["SUMMARY_PRIMARY_URL"] = "https://primary.test/v1"
        os.environ["SUMMARY_FALLBACK_URL"] = "https://fallback.test/v1"
        os.environ["SUMMARY_PRIMARY_MODEL"] = "primary-model"
        os.environ["SUMMARY_FALLBACK_MODEL"] = "fallback-model"
        os.environ["SUMMARY_PRIMARY_KEY"] = "primary-key"
        os.environ["SUMMARY_FALLBACK_KEY"] = "fallback-key"

        monkeypatch.setattr(summarize, "httpx", __import__("httpx"))
        monkeypatch.setattr(summarize.httpx, "post", fake_post)

        refresh._run_refresh()

        cur = conn.cursor()
        cur.execute(
            "SELECT summary, topic_tag, summary_source, summary_model FROM articles"
        )
        summary, tag, source, model = cur.fetchone()
        assert summary == "Fallback summary."
        assert source == "fallback"
        assert model is None
        # Ensure fallback was indeed called.
        assert call_sequence == ["fallback"]
    finally:
        conn.close()
        _teardown_temp_db(db_path, old_env)


def test_both_models_fail_uses_title_and_general_tag(monkeypatch):
    """If primary and fallback both error, summary = truncated title, tag = 'general'."""
    db_path, conn, old_env = _setup_temp_db()
    try:
        title = "A" * 200  # longer than 120 chars
        _mock_feed_single(title)

        def always_error(*args, **kwargs):
            raise __import__("httpx").ConnectError("network down")

        monkeypatch.setattr(summarize, "httpx", __import__("httpx"))
        monkeypatch.setattr(summarize.httpx, "post", always_error)

        refresh._run_refresh()

        cur = conn.cursor()
        cur.execute(
            "SELECT summary, topic_tag, summary_source, summary_model FROM articles"
        )
        summary, tag, source, model = cur.fetchone()
        assert summary == title[:120]
        assert tag == "general"
        assert source == "fallback"
        assert model is None
    finally:
        conn.close()
        _teardown_temp_db(db_path, old_env)


def test_idempotent_summarisation(monkeypatch):
    """Running refresh twice does not change already summarised rows."""
    db_path, conn, old_env = _setup_temp_db()
    try:
        _mock_feed_single("Idempotent test")

        # First run – mock a successful primary call.
        def fake_post(url, json, headers=None, timeout=None):
            class Resp:
                status_code = 200

                def raise_for_status(self):
                    pass

                def json(self):
                    return {"choices": [{"message": {"content": "First summary"}}]}

            return Resp()

        monkeypatch.setattr(summarize, "httpx", __import__("httpx"))
        monkeypatch.setattr(summarize.httpx, "post", fake_post)

        refresh._run_refresh()
        cur = conn.cursor()
        cur.execute("SELECT summary FROM articles")
        first_summary = cur.fetchone()[0]

        # Second run – change the mock to raise if called (should not be called).
        def raise_if_called(*args, **kwargs):
            raise AssertionError("Model call should not happen on idempotent run")

        monkeypatch.setattr(summarize.httpx, "post", raise_if_called)
        refresh._run_refresh()
        cur.execute("SELECT summary FROM articles")
        second_summary = cur.fetchone()[0]
        assert second_summary == first_summary
    finally:
        conn.close()
        _teardown_temp_db(db_path, old_env)

