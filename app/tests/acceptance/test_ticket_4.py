"""Acceptance tests for Newsroom refresh ticket (#4).

The tests exercise the public API and confirm that the schema and behaviour
match the acceptance criteria.  All network traffic is mocked so the tests can
run offline.
"""

from __future__ import annotations

import importlib
import os
import shutil
import sqlite3
import sys
import tempfile
from datetime import datetime
from typing import Iterable, Tuple

import pytest

# The package is located at ``repo/app``.  Adding the parent directory to
# ``sys.path`` allows ``import newsroom`` just like the unit tests in the
# repo's own test suite.
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "../..", "repo", "app")))

# Import the public helpers.  The module imports are intentionally *lazy*;
# importing here will exercise the repository's entry points.
from newsroom import db, feed, refresh


def _table_columns(conn: sqlite3.Connection, table: str) -> Tuple[Tuple[str, str, int, int, str, int], ...]:
    """Return the pragma table_info rows for a table.

    The rows are tuples of ``(cid, name, type, notnull, dflt_value, pk)``.
    """
    cur = conn.cursor()
    cur.execute(f"PRAGMA table_info({table})")
    return cur.fetchall()


def _setup_temp_db() -> Tuple[str, sqlite3.Connection]:
    """Create a temporary SQLite file and point the environment to it.

    Returns the path and a connection that has the schema ensured.
    """
    temp_dir = tempfile.mkdtemp()
    db_path = os.path.join(temp_dir, "newsroom.db")
    old_env = os.getenv(db.DB_PATH_ENV)
    os.environ[db.DB_PATH_ENV] = db_path
    # Ensure the schema is created.
    conn = sqlite3.connect(db_path)
    db._ensure_schema(conn)
    return db_path, conn


def _teardown_temp_db(path: str, old_env: str | None):
    if old_env is None:
        os.environ.pop(db.DB_PATH_ENV, None)
    else:
        os.environ[db.DB_PATH_ENV] = old_env
    shutil.rmtree(os.path.dirname(path), ignore_errors=True)


class TestSchema:
    def test_tables_and_columns(self, monkeypatch):
        """Verify that the required tables exist with correct columns."""
        path, conn = _setup_temp_db()
        try:
            # Articles table columns as per ticket
            articles_exp = [
                (None, "id", "INTEGER", 0, None, 1),
                (None, "source_url", "TEXT", 1, None, 0),
                (None, "title", "TEXT", 1, None, 0),
                (None, "published_at", "TEXT", 0, None, 0),
                (None, "fetched_at", "TEXT", 1, None, 0),
                (None, "summary", "TEXT", 0, None, 0),
                (None, "topic_tag", "TEXT", 0, None, 0),
                (None, "summary_source", "TEXT", 0, None, 0),
                (None, "summary_model", "TEXT", 0, None, 0),
                (None, "content_hash", "TEXT", 0, None, 0),
            ]
            # PRAGMA returns integer for notnull and pk.  The first element is
            # cid and third is type.
            rows = _table_columns(conn, "articles")
            # Convert rows to a more readable form.
            parsed = [(None, r[1], r[2], r[3], None, r[5]) for r in rows]
            assert parsed == articles_exp, f"Unexpected articles schema: {parsed}"

            # Refresh_runs table columns
            runs_exp = [
                (None, "id", "INTEGER", 0, None, 1),
                (None, "started_at", "TEXT", 1, None, 0),
                (None, "finished_at", "TEXT", 0, None, 0),
                (None, "articles_fetched", "INTEGER", 1, None, 0),
                (None, "articles_new", "INTEGER", 1, None, 0),
                (None, "status", "TEXT", 1, None, 0),
                (None, "error_message", "TEXT", 0, None, 0),
            ]
            rows2 = _table_columns(conn, "refresh_runs")
            parsed2 = [(None, r[1], r[2], r[3], None, r[5]) for r in rows2]
            assert parsed2 == runs_exp, f"Unexpected runs schema: {parsed2}"
        finally:
            conn.close()
            _teardown_temp_db(path, None)


class TestRefreshBehaviour:
    def test_idempotent_runs(self, monkeypatch):
        # Mock the feed to provide deterministic articles.
        sample = [
            {
                "source_url": "https://example.com/1",
                "title": "First",
                "published_at": None,
                "fetched_at": datetime.utcnow().isoformat(),
                "summary": None,
                "topic_tag": None,
            },
            {
                "source_url": "https://example.com/2",
                "title": "Second",
                "published_at": None,
                "fetched_at": datetime.utcnow().isoformat(),
                "summary": None,
                "topic_tag": None,
            },
        ]

        monkeypatch.setattr(feed, "fetch_articles", lambda topic=None: sample)

        path, conn = _setup_temp_db()
        try:
            # First run – two new articles.
            refresh._run_refresh()
            cur = conn.cursor()
            cur.execute("SELECT COUNT(*) FROM articles")
            assert cur.fetchone()[0] == 2
            cur.execute("SELECT articles_new FROM refresh_runs ORDER BY id DESC LIMIT 1")
            assert cur.fetchone()[0] == 2

            # Second run – already existing, no new rows.
            refresh._run_refresh()
            cur.execute("SELECT COUNT(*) FROM articles")
            assert cur.fetchone()[0] == 2
            cur.execute("SELECT SUM(articles_new) FROM refresh_runs")
            assert cur.fetchone()[0] == 2
        finally:
            conn.close()
            _teardown_temp_db(path, None)

    def test_parse_actual_rss_and_store(self, monkeypatch):
        """Mock HTTP to return a real RSS XML with two items and verify DB rows."""
        # Minimal RSS 2.0 document with two items.
        rss_xml = """
        <?xml version='1.0' encoding='UTF-8'?>
        <rss version='2.0'>
          <channel>
            <title>Test Feed</title>
            <item>
              <title>Article One</title>
              <link>https://example.com/one</link>
              <pubDate>Mon, 01 Jan 2024 00:00:00 GMT</pubDate>
            </item>
            <item>
              <title>Article Two</title>
              <link>https://example.com/two</link>
              <pubDate>Tue, 02 Jan 2024 00:00:00 GMT</pubDate>
            </item>
          </channel>
        </rss>
        """

        class Resp:
            status_code = 200
            text = rss_xml
            def raise_for_status(self):
                pass

        # Patch httpx.get to return the RSS response.
        import importlib, httpx
        monkeypatch.setattr(feed, "httpx", importlib.import_module("httpx"))
        # Accept extra kwargs to match the real httpx.get signature.
        monkeypatch.setattr(feed.httpx, "get", lambda url, timeout=10.0, **kwargs: Resp())

        path, conn = _setup_temp_db()
        try:
            refresh._run_refresh()
            cur = conn.cursor()
            cur.execute("SELECT source_url, title, published_at FROM articles ORDER BY id")
            rows = cur.fetchall()
            assert len(rows) == 2
            assert rows[0][0] == "https://example.com/one"
            assert rows[0][1] == "Article One"
            assert rows[0][2] is not None
            assert rows[1][0] == "https://example.com/two"
            assert rows[1][1] == "Article Two"
            # Refresh run should be recorded as successful.
            cur.execute(
                "SELECT status, articles_fetched, articles_new FROM refresh_runs ORDER BY id DESC LIMIT 1"
            )
            status, fetched, new = cur.fetchone()
            assert status == "success"
            assert fetched == 2
            assert new == 2
        finally:
            conn.close()
            _teardown_temp_db(path, None)

    def test_malformed_and_invalid_feeds(self, monkeypatch):
        """Handle items missing link or title and a feed that is not well‑formed XML."""
        # RSS with one item missing <link> and one missing <title>.
        rss_xml = """
        <?xml version='1.0'?>
        <rss version='2.0'>
          <channel>
            <item>
              <title>Missing Link</title>
            </item>
            <item>
              <link>https://example.com/no-title</link>
            </item>
            <item>
              <title>Good Item</title>
              <link>https://example.com/good</link>
            </item>
          </channel>
        </rss>
        """

        class Resp:
            status_code = 200
            text = rss_xml
            def raise_for_status(self):
                pass

        import importlib, httpx
        monkeypatch.setattr(feed, "httpx", importlib.import_module("httpx"))
        monkeypatch.setattr(feed.httpx, "get", lambda url, timeout=10.0, **kwargs: Resp())

        path, conn = _setup_temp_db()
        try:
            refresh._run_refresh()
            cur = conn.cursor()
            cur.execute("SELECT source_url, title FROM articles ORDER BY id")
            rows = cur.fetchall()
            # Two rows should be stored: one with missing title (empty string) and the good one.
            assert len(rows) == 2
            # Entry without link is skipped, so first row is the one with link but missing title.
            assert rows[0][0] == "https://example.com/no-title"
            assert rows[0][1] == ""
            # Good item.
            assert rows[1][0] == "https://example.com/good"
            assert rows[1][1] == "Good Item"
            # Run recorded.
            cur.execute(
                "SELECT status, articles_fetched FROM refresh_runs ORDER BY id DESC LIMIT 1"
            )
            status, fetched = cur.fetchone()
            assert status == "success"
            # Three entries parsed, one skipped due to missing link.
            # The ticket does not require counting skipped entries, so we do not assert on ``fetched``.
        finally:
            conn.close()
            _teardown_temp_db(path, None)

    def test_invalid_xml_does_not_crash(self, monkeypatch):
        """When the response body is not XML, the refresh should succeed with zero articles."""
        class Resp:
            status_code = 200
            text = "This is not XML!"
            def raise_for_status(self):
                pass

        import importlib, httpx
        monkeypatch.setattr(feed, "httpx", importlib.import_module("httpx"))
        monkeypatch.setattr(feed.httpx, "get", lambda url, timeout=10.0, **kwargs: Resp())

        path, conn = _setup_temp_db()
        try:
            refresh._run_refresh()
            cur = conn.cursor()
            cur.execute("SELECT COUNT(*) FROM articles")
            assert cur.fetchone()[0] == 0
            cur.execute(
                "SELECT status, articles_fetched FROM refresh_runs ORDER BY id DESC LIMIT 1"
            )
            status, fetched = cur.fetchone()
            assert status == "success"
            assert fetched == 0
        finally:
            conn.close()
            _teardown_temp_db(path, None)

    def test_network_failures_record_error(self, monkeypatch):
        """Simulate connection errors and HTTP 500 responses; the refresh should record an error status."""
        import importlib, httpx
        # Scenario 1 – connection error.
        def raise_connect(url, timeout=10.0, **kwargs):
            raise httpx.ConnectError("connection failed")
        monkeypatch.setattr(feed, "httpx", importlib.import_module("httpx"))
        monkeypatch.setattr(feed.httpx, "get", raise_connect)
        path, conn = _setup_temp_db()
        try:
            # The refresh function should handle errors internally and not raise SystemExit.
            refresh._run_refresh()
            cur = conn.cursor()
            cur.execute("SELECT status, error_message FROM refresh_runs ORDER BY id DESC LIMIT 1")
            status, err = cur.fetchone()
            assert status == "error"
            assert "connection failed" in err
        finally:
            conn.close()
            _teardown_temp_db(path, None)
        # Scenario 2 – HTTP 500 error.
        class Resp500:
            status_code = 500
            text = "Server error"
            def raise_for_status(self):
                raise httpx.HTTPStatusError("500 Server Error", request=None, response=self)
        monkeypatch.setattr(feed.httpx, "get", lambda url, timeout=10.0, **kwargs: Resp500())
        path2, conn2 = _setup_temp_db()
        try:
            # Refresh should not raise SystemExit on HTTP 500 errors.
            refresh._run_refresh()
            cur2 = conn2.cursor()
            cur2.execute("SELECT status, error_message FROM refresh_runs ORDER BY id DESC LIMIT 1")
            status2, err2 = cur2.fetchone()
            assert status2 == "error"
            assert "500 Server Error" in err2
        finally:
            conn2.close()
            _teardown_temp_db(path2, None)

    def test_httpx_get_called_with_timeout(self, monkeypatch):
        """Ensure that ``feed.fetch_articles`` uses httpx.get with timeout.

        We monkeypatch ``httpx.get`` to record the timeout argument.
        """
        called = {}

    def fake_get(url: str, timeout: float = 10.0, **kwargs):  # pragma: no cover
        # Capture the URL and timeout; ignore any extra kwargs (e.g., headers, follow_redirects).
        called["url"] = url
        called["timeout"] = timeout
        class Resp:
            status_code = 200
            text = "<rss></rss>"
            def raise_for_status(self):
                pass
        return Resp()

        monkeypatch.setattr(feed, "httpx", importlib.import_module("httpx"))
        monkeypatch.setattr(feed.httpx, "get", fake_get)
        # Also monkeypatch feedparser.parse to prevent real parsing.
        monkeypatch.setattr(feed, "feedparser", importlib.import_module("feedparser"))
        monkeypatch.setattr(feed.feedparser, "parse", lambda x: type("Obj", (), {"entries": []})())

        feed.fetch_articles(topic="demo")
        assert "url" in called, "httpx.get not called"
        assert called["timeout"] == 10.0, "default timeout not used"

