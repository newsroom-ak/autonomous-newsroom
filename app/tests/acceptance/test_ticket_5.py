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
                    # Return a JSON‑structured reply as required by Ticket #25.
                    # Include a deterministic tag so we can assert it later.
                    return {"choices": [{"message": {"content": "{\"summary\": \"A concise summary.\", \"tag\": \"research\"}"}}]}

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
        # The summary should be extracted from the JSON payload and stored unchanged.
        assert summary == "A concise summary."
        # The tag comes from the JSON reply.
        assert tag == "research"
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
                        # Return a JSON reply with a tag.
                        return {"choices": [{"message": {"content": "{\"summary\": \"Fallback summary.\", \"tag\": \"models\"}"}}]}

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
        # Per updated acceptance criteria, a fallback model still counts as a "model" source
        # and the name of the model used should be recorded.
        assert source == "model"
        assert model == "fallback-model"
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

# ---------------------------------------------------------------------------
# Additional product‑owner criteria tests (added per reviewer request)
# ---------------------------------------------------------------------------

def test_primary_429_triggers_fallback(monkeypatch):
    """Primary returning 429 should cause fallback model call and record its name."""
    db_path, conn, old_env = _setup_temp_db()
    try:
        _mock_feed_single("Primary 429 fallback test")

        def fake_post(url, json, headers=None, timeout=None):
            if "primary" in url:
                # Simulate 429 HTTPStatusError
                class Resp:
                    status_code = 429

                    def raise_for_status(self):
                        raise __import__("httpx").HTTPStatusError(
                            "429 Too Many Requests", request=None, response=self
                        )

                raise Resp().raise_for_status()
            else:
                class Resp:
                    status_code = 200

                    def raise_for_status(self):
                        pass

                    def json(self):
                        return {"choices": [{"message": {"content": "Fallback from 429."}}]}

                return Resp()

        os.environ["SUMMARY_PRIMARY_URL"] = "https://primary.test/v1"
        os.environ["SUMMARY_FALLBACK_URL"] = "https://fallback.test/v1"
        os.environ["SUMMARY_PRIMARY_MODEL"] = "primary-model"
        os.environ["SUMMARY_FALLBACK_MODEL"] = "fallback-model"

        monkeypatch.setattr(summarize, "httpx", __import__("httpx"))
        monkeypatch.setattr(summarize.httpx, "post", fake_post)

        refresh._run_refresh()

        cur = conn.cursor()
        cur.execute(
            "SELECT summary, summary_source, summary_model FROM articles"
        )
        summary, source, model = cur.fetchone()
        assert summary == "Fallback from 429."
        assert source == "model"
        assert model == "fallback-model"
    finally:
        conn.close()
        _teardown_temp_db(db_path, old_env)


def test_primary_timeout_triggers_fallback(monkeypatch):
    """Primary raising a timeout exception should also use the fallback model."""
    db_path, conn, old_env = _setup_temp_db()
    try:
        _mock_feed_single("Primary timeout fallback test")

        def fake_post(url, json, headers=None, timeout=None):
            if "primary" in url:
                raise __import__("httpx").TimeoutException("timeout")
            else:
                class Resp:
                    status_code = 200

                    def raise_for_status(self):
                        pass

                    def json(self):
                        return {"choices": [{"message": {"content": "Fallback from timeout."}}]}

                return Resp()

        os.environ["SUMMARY_PRIMARY_URL"] = "https://primary.test/v1"
        os.environ["SUMMARY_FALLBACK_URL"] = "https://fallback.test/v1"
        os.environ["SUMMARY_PRIMARY_MODEL"] = "primary-model"
        os.environ["SUMMARY_FALLBACK_MODEL"] = "fallback-model"

        monkeypatch.setattr(summarize, "httpx", __import__("httpx"))
        monkeypatch.setattr(summarize.httpx, "post", fake_post)

        refresh._run_refresh()

        cur = conn.cursor()
        cur.execute(
            "SELECT summary, summary_source, summary_model FROM articles"
        )
        summary, source, model = cur.fetchone()
        assert summary == "Fallback from timeout."
        assert source == "model"
        assert model == "fallback-model"
    finally:
        conn.close()
        _teardown_temp_db(db_path, old_env)


def test_max_summaries_limit(monkeypatch):
    """Setting NEWSROOM_MAX_SUMMARIES limits the number of model calls per refresh."""
    db_path, conn, old_env = _setup_temp_db()
    try:
        # Create three articles with distinct URLs.
        articles = []
        for i in range(3):
            articles.append({
                "source_url": f"https://example.com/{i}",
                "title": f"Article {i}",
                "published_at": None,
                "fetched_at": None,
                "summary": None,
                "topic_tag": None,
            })
        feed.fetch_articles = lambda: articles

        call_counter = {"calls": 0}

        def fake_post(url, json, headers=None, timeout=None):
            call_counter["calls"] += 1
            class Resp:
                status_code = 200

                def raise_for_status(self):
                    pass

                def json(self):
                    return {"choices": [{"message": {"content": f"Summary {json['model']}"}}]}

            return Resp()

        monkeypatch.setattr(summarize, "httpx", __import__("httpx"))
        monkeypatch.setattr(summarize.httpx, "post", fake_post)

        # Set limit AFTER modules have been imported.
        monkeypatch.setenv("NEWSROOM_MAX_SUMMARIES", "2")
        import importlib
        importlib.reload(summarize)

        refresh._run_refresh()
        # Only two model calls should have been made.
        assert call_counter["calls"] == 2
    finally:
        conn.close()
        _teardown_temp_db(db_path, old_env)


def test_resummarize_on_title_change(monkeypatch):
    """Changing an article title triggers a re‑summarisation; unchanged title does not."""
    db_path, conn, old_env = _setup_temp_db()
    try:
        url = "https://example.com/article"
        # First fetch with title A.
        _mock_feed_single("Title A", url)

        call_counter = {"calls": 0}

        def fake_post(url, json, headers=None, timeout=None):
            call_counter["calls"] += 1
            class Resp:
                status_code = 200

                def raise_for_status(self):
                    pass

                def json(self):
                    return {"choices": [{"message": {"content": "Summary A"}}]}

            return Resp()

        monkeypatch.setattr(summarize, "httpx", __import__("httpx"))
        monkeypatch.setattr(summarize.httpx, "post", fake_post)

        # First refresh – creates article and summarises.
        refresh._run_refresh()
        # Second refresh with same title – should not invoke model again.
        refresh._run_refresh()
        assert call_counter["calls"] == 1

        # Change title to B and refresh – should invoke model once more.
        _mock_feed_single("Title B", url)
        refresh._run_refresh()
        assert call_counter["calls"] == 2
    finally:
        conn.close()
        _teardown_temp_db(db_path, old_env)

