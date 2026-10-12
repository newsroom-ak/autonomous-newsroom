'''Acceptance tests for ticket #7 – server‑rendered public homepage.

These tests verify that the FastAPI application serves an HTML page at ``GET /``
that lists recent articles using the shared ``_fetch_articles_data`` logic, and
that the rendered content obeys the acceptance criteria described in
``TICKET.md``.
'''

from __future__ import annotations

import os
import shutil
import sqlite3
import sys
import tempfile
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

# Add the repository's ``app`` directory to ``sys.path`` so ``import newsroom`` works.
repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "repo", "app"))
sys.path.append(repo_root)

from newsroom import db, main  # noqa: E402


def _setup_temp_db():
    """Create a temporary SQLite DB and point ``NEWSROOM_DB`` at it.

    Returns ``(db_path, conn, old_env)`` where ``old_env`` is the previous value
    of the environment variable so the test can restore it afterwards.
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
    shutil.rmtree(os.path.dirname(path), ignore_errors=True)


def _insert_article(conn, *, source_url: str, title: str, published_at: str | None = None,
                    fetched_at: str | None = None, summary: str | None = None,
                    topic_tag: str | None = None, summary_source: str | None = None,
                    summary_model: str | None = None):
    """Insert a row via ``db.upsert_article`` – it will fill missing timestamps."""
    db.upsert_article(
        source_url=source_url,
        title=title,
        published_at=published_at,
        fetched_at=fetched_at,
        summary=summary,
        topic_tag=topic_tag,
        summary_source=summary_source,
        summary_model=summary_model,
    )
    # The helper uses its own connection internally; commit to make data visible.
    conn.commit()


def _insert_refresh_run(conn, finished_at_iso: str | None):
    """Record a successful refresh run; ``finished_at_iso`` may be ``None``."""
    db.insert_refresh_run(
        started_at=datetime.utcnow().isoformat(),
        finished_at=finished_at_iso,
        articles_fetched=0,
        articles_new=0,
        status="success",
    )
    conn.commit()


@pytest.fixture(autouse=True)
def _reset_env():
    """Ensure the ``NEWSROOM_DB`` env var does not leak between tests."""
    original = os.getenv(db.DB_PATH_ENV)
    yield
    if original is None:
        os.environ.pop(db.DB_PATH_ENV, None)
    else:
        os.environ[db.DB_PATH_ENV] = original


def test_homepage_empty_db_shows_no_articles():
    """When the DB has no articles, the page displays the empty‑state message."""
    db_path, conn, old = _setup_temp_db()
    try:
        client = TestClient(main.app)
        resp = client.get("/")
        assert resp.status_code == 200
        html = resp.text
        assert "No articles yet" in html
        # Header should mention lack of refresh data.
        assert "No refresh data available" in html
    finally:
        conn.close()
        _teardown_temp_db(db_path, old)


def test_homepage_renders_articles_and_header(monkeypatch):
    """Full rendering test covering article fields and the header timestamp.

    * Inserts 12 articles (more than the 10‑article limit).
    * Inserts a refresh run 2 hours ago.
    * Patches ``datetime.utcnow`` used by the API to a deterministic value
      so the ``updated_ago`` string is predictable.
    * Verifies that each rendered article contains the summary, badge, and a
      safe source link.
    * Confirms that the header's "Updated … ago" matches the API response.
    """
    db_path, conn, old = _setup_temp_db()
    try:
        base_dt = datetime(2026, 10, 11, 12, 0, tzinfo=timezone.utc)
        # Insert 12 articles with varied data.
        for i in range(12):
            src = f"https://example.com/article{i}"
            title = f"Title {i}"
            # Every 3rd article uses a fallback summary.
            src_summary = "fallback" if i % 3 == 0 else "model"
            model_note = None if i % 3 == 0 else f"model-{i}"
            _insert_article(
                conn,
                source_url=src,
                title=title,
                published_at=(base_dt.replace(hour=10 - i)).isoformat() if i % 2 == 0 else None,
                fetched_at=base_dt.isoformat(),
                summary=f"Summary {i}",
                topic_tag=f"tag{i}",
                summary_source=src_summary,
                summary_model=model_note,
            )
        # Record a refresh run 2 hours earlier than ``base_dt``.
        finish_iso = (base_dt.replace(hour=10)).replace(tzinfo=None).isoformat()
        _insert_refresh_run(conn, finish_iso)
        # Patch datetime.utcnow used inside the API/helpers.
        class FixedDateTime(datetime):
            @classmethod
            def utcnow(cls):  # type: ignore[override]
                return base_dt
        monkeypatch.setattr("newsroom.api.datetime", FixedDateTime)
        client = TestClient(main.app)
        # Get homepage HTML.
        html_resp = client.get("/")
        assert html_resp.status_code == 200
        html = html_resp.text
        # Header should show "Updated 2 hours ago".
        assert "Updated 2 hours ago" in html
        # Should render at most 10 articles.
        assert html.count('<div class="article">') == 10
        # Verify each article block contains expected pieces.
        for i in range(10):
            # Only check the first 10 (newest ones).
            assert f"Title {i}" in html
            if i % 3 == 0:
                # Fallback articles: headline and label, no stored summary.
                assert "summary unavailable – showing headline" in html
                assert f"Summary {i}" not in html
            else:
                # Model articles retain stored summary and model note.
                assert f"Summary {i}" in html
                assert f"(model: model-{i})" in html
            assert f"tag{i}" in html
            # Source link must be present and safe.
            assert f'href="https://example.com/article{i}"' in html
        # Compare header ``updated_ago`` with API payload for consistency.
        api_resp = client.get("/api/articles")
        assert api_resp.status_code == 200
        api_data = api_resp.json()
        assert api_data["updated_ago"] == "2 hours ago"
    finally:
        conn.close()
        _teardown_temp_db(db_path, old)


def test_homepage_escapes_html_and_filters_bad_links(monkeypatch):
    """Ensure Jinja2 auto‑escaping prevents XSS and that non‑http URLs are omitted."""
    db_path, conn, old = _setup_temp_db()
    try:
        # Insert an article with malicious title and a javascript URL.
        _insert_article(
            conn,
            source_url="javascript:alert('xss')",
            title="<script>alert('title')</script>",
            summary="<b>bold</b>",
            topic_tag="<i>tag</i>",
            summary_source="model",
            summary_model="model-x",
        )
        client = TestClient(main.app)
        html = client.get("/").text
        # The raw HTML should be escaped – no literal <script> tags.
        assert "<script>" not in html
        assert "&lt;script&gt;alert(&#39;title&#39;)&lt;/script&gt;" in html
        # The malicious URL should not render a link element.
        assert "javascript:alert('xss')" not in html
        # The badge should contain escaped tag text.
        assert "&lt;i&gt;tag&lt;/i&gt;" in html
    finally:
        conn.close()
        _teardown_temp_db(db_path, old)
