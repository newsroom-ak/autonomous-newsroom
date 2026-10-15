"""Additional acceptance tests exercising the new summarisation contract.

These tests focus on the behaviours introduced by ticket #25 that are not
covered by the existing per‑ticket acceptance files. They use the same helper
functions as the other acceptance tests (temporary SQLite DB, ``_mock_feed_single``
and ``monkeypatch``) to stay isolated and avoid leaking state.
"""

from __future__ import annotations

import os
import sqlite3
import sys
import tempfile
from typing import Tuple

import pytest

# Add the repository's ``app`` directory to ``sys.path`` so that ``import newsroom`` works.
sys.path.append(
    os.path.abspath(os.path.join(os.path.dirname(__file__), "../repo", "app"))
)

from newsroom import db, refresh, summarize, feed  # noqa: E402


def _setup_temp_db() -> Tuple[str, sqlite3.Connection, str | None]:
    """Create a temporary SQLite DB and point the ``NEWSROOM_DB`` env var to it.

    Returns the path, an open connection (schema already ensured) and the
    previous value of ``NEWSROOM_DB`` so it can be restored.
    """
    temp_dir = tempfile.mkdtemp()
    db_path = os.path.join(temp_dir, "newsroom.db")
    old_env = os.getenv(db.DB_PATH_ENV)
    os.environ[db.DB_PATH_ENV] = db_path
    conn = sqlite3.connect(db_path)
    db._ensure_schema(conn)
    return db_path, conn, old_env


def _teardown_temp_db(path: str, old_env: str | None):
    if old_env is None:
        os.environ.pop(db.DB_PATH_ENV, None)
    else:
        os.environ[db.DB_PATH_ENV] = old_env
    # ``temp_dir`` is the parent of the DB file.
    shutil.rmtree(os.path.dirname(path), ignore_errors=True)


def _mock_feed_single(title: str, url: str = "https://example.com/article"):
    """Patch ``feed.fetch_articles`` to return a single article.
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


def test_multiline_summary(monkeypatch):
    """A valid JSON reply whose ``summary`` contains a newline should be stored as a single line."""
    db_path, conn, old_env = _setup_temp_db()
    try:
        _mock_feed_single("Multiline test")

        def fake_post(url, json, headers=None, timeout=None):
            class Resp:
                status_code = 200

                def raise_for_status(self):
                    pass

                def json(self):
                    # The raw model content includes a newline inside the summary.
                    return {
                        "choices": [
                            {"message": {"content": "{\"summary\": \"Line one.\nLine two.\", \"tag\": \"research\"}"}}
                        ]
                    }

            return Resp()

        # Ensure environment variables are set.
        monkeypatch.setenv("SUMMARY_PRIMARY_URL", "https://primary.test/v1")
        monkeypatch.setenv("SUMMARY_PRIMARY_MODEL", "primary-model")
        monkeypatch.setattr(summarize, "httpx", __import__("httpx"))
        monkeypatch.setattr(summarize.httpx, "post", fake_post)

        refresh._run_refresh()
        cur = conn.cursor()
        cur.execute("SELECT summary FROM articles")
        stored = cur.fetchone()[0]
        # Newlines should be collapsed to a single space.
        assert stored == "Line one. Line two."
    finally:
        conn.close()
        _teardown_temp_db(db_path, old_env)


def test_tag_non_string_is_general(monkeypatch):
    """When the ``tag`` field is not a string (e.g., a number, list or null) the stored tag is ``general``."""
    db_path, conn, old_env = _setup_temp_db()
    try:
        _mock_feed_single("Non‑string tag test")

        def fake_post(url, json, headers=None, timeout=None):
            class Resp:
                status_code = 200

                def raise_for_status(self):
                    pass

                def json(self):
                    # Tag is a number – invalid per schema.
                    return {"choices": [{"message": {"content": "{\"summary\": \"Valid summary.\", \"tag\": 123}"}}]}

            return Resp()

        monkeypatch.setenv("SUMMARY_PRIMARY_URL", "https://primary.test/v1")
        monkeypatch.setenv("SUMMARY_PRIMARY_MODEL", "primary-model")
        monkeypatch.setattr(summarize, "httpx", __import__("httpx"))
        monkeypatch.setattr(summarize.httpx, "post", fake_post)

        refresh._run_refresh()
        cur = conn.cursor()
        cur.execute("SELECT topic_tag FROM articles")
        tag = cur.fetchone()[0]
        assert tag == "general"
    finally:
        conn.close()
        _teardown_temp_db(db_path, old_env)


def test_invalid_summary_triggers_fallback(monkeypatch):
    """A primary reply with an empty/whitespace summary is treated as unusable and the fallback model is used."""
    db_path, conn, old_env = _setup_temp_db()
    try:
        _mock_feed_single("Invalid primary summary")

        def fake_post(url, json, headers=None, timeout=None):
            if json["model"] == "primary-model":
                # Primary returns whitespace summary.
                class Resp:
                    status_code = 200

                    def raise_for_status(self):
                        pass

                    def json(self):
                        return {"choices": [{"message": {"content": "{\"summary\": \"   \", \"tag\": \"research\"}"}}]}

                return Resp()
            else:
                # Fallback returns a usable summary.
                class Resp:
                    status_code = 200

                    def raise_for_status(self):
                        pass

                    def json(self):
                        return {"choices": [{"message": {"content": "{\"summary\": \"Fallback summary.\", \"tag\": \"models\"}"}}]}

                return Resp()

        monkeypatch.setenv("SUMMARY_PRIMARY_URL", "https://primary.test/v1")
        monkeypatch.setenv("SUMMARY_FALLBACK_URL", "https://fallback.test/v1")
        monkeypatch.setenv("SUMMARY_PRIMARY_MODEL", "primary-model")
        monkeypatch.setenv("SUMMARY_FALLBACK_MODEL", "fallback-model")
        monkeypatch.setattr(summarize, "httpx", __import__("httpx"))
        monkeypatch.setattr(summarize.httpx, "post", fake_post)

        refresh._run_refresh()
        cur = conn.cursor()
        cur.execute("SELECT summary, summary_source, summary_model FROM articles")
        summary, source, model = cur.fetchone()
        assert summary == "Fallback summary."
        assert source == "model"
        assert model == "fallback-model"
    finally:
        conn.close()
        _teardown_temp_db(db_path, old_env)


def test_malformed_json_fallback(monkeypatch):
    """If the primary model returns malformed JSON, the fallback model is used."""
    db_path, conn, old_env = _setup_temp_db()
    try:
        _mock_feed_single("Malformed JSON primary")

        def fake_post(url, json, headers=None, timeout=None):
            if json["model"] == "primary-model":
                class Resp:
                    status_code = 200

                    def raise_for_status(self):
                        pass

                    def json(self):
                        # Return a plain string that is not JSON.
                        raise ValueError("Invalid JSON")

                return Resp()
            else:
                class Resp:
                    status_code = 200

                    def raise_for_status(self):
                        pass

                    def json(self):
                        return {"choices": [{"message": {"content": "{\"summary\": \"Good fallback.\", \"tag\": \"education\"}"}}]}

                return Resp()

        monkeypatch.setenv("SUMMARY_PRIMARY_URL", "https://primary.test/v1")
        monkeypatch.setenv("SUMMARY_FALLBACK_URL", "https://fallback.test/v1")
        monkeypatch.setenv("SUMMARY_PRIMARY_MODEL", "primary-model")
        monkeypatch.setenv("SUMMARY_FALLBACK_MODEL", "fallback-model")
        monkeypatch.setattr(summarize, "httpx", __import__("httpx"))
        monkeypatch.setattr(summarize.httpx, "post", fake_post)

        refresh._run_refresh()
        cur = conn.cursor()
        cur.execute("SELECT summary, summary_source, summary_model FROM articles")
        summary, source, model = cur.fetchone()
        assert summary == "Good fallback."
        assert source == "model"
        assert model == "fallback-model"
    finally:
        conn.close()
        _teardown_temp_db(db_path, old_env)


def test_whole_word_truncation(monkeypatch):
    """A summary longer than 160 characters is truncated at the last word boundary and ends with an ellipsis."""
    db_path, conn, old_env = _setup_temp_db()
    try:
        _mock_feed_single("Truncation test")

        long_text = (
            "Word " * 40
        )  # 200 characters with spaces; ensures we have multiple word boundaries.

        def fake_post(url, json, headers=None, timeout=None):
            class Resp:
                status_code = 200

                def raise_for_status(self):
                    pass

                def json(self):
                    return {"choices": [{"message": {"content": f"{{\"summary\": \"{long_text}\", \"tag\": \"research\"}}"}}]}

            return Resp()

        monkeypatch.setenv("SUMMARY_PRIMARY_URL", "https://primary.test/v1")
        monkeypatch.setenv("SUMMARY_PRIMARY_MODEL", "primary-model")
        monkeypatch.setattr(summarize, "httpx", __import__("httpx"))
        monkeypatch.setattr(summarize.httpx, "post", fake_post)

        refresh._run_refresh()
        cur = conn.cursor()
        cur.execute("SELECT summary FROM articles")
        stored = cur.fetchone()[0]
        assert len(stored) <= 160
        # The stored text should end with an ellipsis only if truncation occurred.
        if len(stored) < len(long_text.strip()):
            assert stored.endswith("…")
            # Verify the prefix before the ellipsis is a whole‑word cut.
            prefix = stored.rstrip("…")
            assert long_text.startswith(prefix)
        else:
            # No truncation needed – the text should be unchanged.
            assert stored == long_text.strip()
    finally:
        conn.close()
        _teardown_temp_db(db_path, old_env)


def test_fallback_contract_literal(monkeypatch):
    """When both models fail, the stored summary equals the first 120 characters of the title (literal expectation)."""
    db_path, conn, old_env = _setup_temp_db()
    try:
        long_title = "T" * 200  # Longer than 120 characters.
        _mock_feed_single(long_title)

        def always_error(url, json, headers=None, timeout=None):
            import httpx
            raise httpx.ConnectError("network down")

        monkeypatch.setenv("SUMMARY_PRIMARY_URL", "https://primary.test/v1")
        monkeypatch.setenv("SUMMARY_FALLBACK_URL", "https://fallback.test/v1")
        monkeypatch.setattr(summarize, "httpx", __import__("httpx"))
        monkeypatch.setattr(summarize.httpx, "post", always_error)

        refresh._run_refresh()
        cur = conn.cursor()
        cur.execute("SELECT summary, topic_tag, summary_source, summary_model FROM articles")
        summary, tag, source, model = cur.fetchone()
        assert summary == long_title[:120]
        assert tag == "general"
        assert source == "fallback"
        assert model is None
    finally:
        conn.close()
        _teardown_temp_db(db_path, old_env)

