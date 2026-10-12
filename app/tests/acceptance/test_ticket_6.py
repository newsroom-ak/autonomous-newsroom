"""Acceptance tests for ticket #6 – JSON API endpoint.

The tests exercise the public FastAPI endpoint ``GET /api/articles`` defined in
``newsroom.api``. They verify the JSON response shape, ordering, limits, and the
deterministic ``updated_ago`` field.

All external I/O is avoided – a temporary SQLite database is created for each
test and the ``NEWSROOM_DB`` environment variable is pointed at it. The FastAPI
application is imported from ``newsroom.main`` which provides a reusable ``app``
object.
"""

from __future__ import annotations

import os
import shutil
import sqlite3
import sys
import tempfile
from datetime import datetime, timezone
from typing import Tuple

import pytest
from fastapi.testclient import TestClient

# Add the repository's ``app`` directory to ``sys.path`` so that ``import newsroom`` works.
repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "repo", "app"))
sys.path.append(repo_root)

from newsroom import db, api, main  # noqa: E402


def _setup_temp_db() -> Tuple[str, sqlite3.Connection, str | None]:
    """Create a temporary SQLite file and point the ``NEWSROOM_DB`` env var to it.

    Returns the path, an open connection (schema already ensured) and the previous
    value of ``NEWSROOM_DB`` so it can be restored after the test.
    """
    temp_dir = tempfile.mkdtemp()
    db_path = os.path.join(temp_dir, "newsroom.db")
    old_env = os.getenv(db.DB_PATH_ENV)
    os.environ[db.DB_PATH_ENV] = db_path
    # Ensure the schema is created.
    conn = sqlite3.connect(db_path)
    db._ensure_schema(conn)
    return db_path, conn, old_env


def _teardown_temp_db(path: str, old_env: str | None):
    if old_env is None:
        os.environ.pop(db.DB_PATH_ENV, None)
    else:
        os.environ[db.DB_PATH_ENV] = old_env
    shutil.rmtree(os.path.dirname(path), ignore_errors=True)


def _insert_article(
    *,
    source_url: str,
    title: str,
    published_at: str | None = None,
    fetched_at: str | None = None,
    summary: str = "summary",
    topic_tag: str = "tag",
    summary_source: str = "model",
    summary_model: str = "model-x",
) -> None:
    """Utility wrapper around ``db.upsert_article`` that forces a summary.
    ``upsert_article`` inserts the article and marks it as new.
    """
    # ``upsert_article`` will fill ``fetched_at`` if omitted.
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


def _insert_refresh_run(finished_at_iso: str) -> None:
    """Insert a successful refresh run with the given ``finished_at`` timestamp."""
    db.insert_refresh_run(
        started_at=datetime.utcnow().isoformat(),
        finished_at=finished_at_iso,
        articles_fetched=0,
        articles_new=0,
        status="success",
    )


@pytest.fixture(autouse=True)
def _reset_env():
    """Ensure the environment variable does not leak between tests."""
    original = os.getenv(db.DB_PATH_ENV)
    yield
    if original is None:
        os.environ.pop(db.DB_PATH_ENV, None)
    else:
        os.environ[db.DB_PATH_ENV] = original


def test_empty_database_returns_empty_response():
    """When there are no articles and no refresh runs, the API returns empty list and null timestamps."""
    db_path, conn, old = _setup_temp_db()
    try:
        client = TestClient(main.app)
        resp = client.get("/api/articles")
        assert resp.status_code == 200
        data = resp.json()
        assert data["articles"] == []
        assert data["last_refresh"] is None
        assert data["updated_ago"] is None
    finally:
        conn.close()
        _teardown_temp_db(db_path, old)


def test_articles_limit_and_ordering(monkeypatch):
    """API returns at most 10 articles, newest first based on parsed dates.

    Articles with unparsable ``published_at`` fall back to ``fetched_at``.
    """
    db_path, conn, old = _setup_temp_db()
    try:
        # Insert 12 articles with varying dates.
        base_dt = datetime(2026, 10, 11, 12, 0, tzinfo=timezone.utc)
        for i in range(12):
            # Every third article has an invalid published_at.
            pub = None if i % 3 == 0 else (base_dt.replace(hour=12 - i)).isoformat()
            fetch = (base_dt.replace(hour=0, minute=i)).isoformat()
            _insert_article(
                source_url=f"https://example.com/{i}",
                title=f"Article {i}",
                published_at=pub,
                fetched_at=fetch,
                summary=f"summary {i}",
                topic_tag=f"tag{i}",
                summary_source="model",
                summary_model="model-x",
            )

        # Insert a refresh run 2 hours ago.
        finish_iso = (base_dt.replace(hour=10)).replace(tzinfo=None).isoformat()
        _insert_refresh_run(finish_iso)

        # Patch datetime used inside the API to a deterministic clock.
        # The original stub replaced the entire ``datetime`` module with a tiny
        # object that only provided ``utcnow``.  Because ``api`` also relies on the
        # ``datetime`` constructor (e.g., ``datetime(1970, 1, 1, tzinfo=...)``) for a
        # fallback sort key, that stub caused ``TypeError: dt() takes no arguments``.
        #
        # We instead patch ``api.datetime`` with a subclass of the real
        # ``datetime.datetime`` that overrides only the ``utcnow`` classmethod.
        # All other behaviours (construction, ``fromisoformat`` etc.) remain
        # unchanged, so the code under test works while the current time is
        # deterministic.
        class FixedDateTime(datetime):
            @classmethod
            def utcnow(cls):  # type: ignore[override]
                return base_dt

        monkeypatch.setattr(api, "datetime", FixedDateTime)

        client = TestClient(main.app)
        resp = client.get("/api/articles")
        assert resp.status_code == 200
        data = resp.json()

        # Verify limit.
        assert len(data["articles"]) == 10

        # Verify ordering: newest first by the parsed datetime.
        # Extract the ISO strings from the response for comparison.
        returned_dates = [a["published_at"] for a in data["articles"]]
        # Re‑compute the expected order using the same parsing logic.
        def parse(val: str | None):
            return api._parse_datetime(val) or api._parse_datetime(None)

        # Build list of (source_url, dt) from DB rows.
        cur = conn.cursor()
        cur.execute(
            "SELECT source_url, published_at, fetched_at FROM articles WHERE summary IS NOT NULL"
        )
        rows = cur.fetchall()
        sorted_rows = sorted(
            rows,
            key=lambda r: api._parse_datetime(r[1]) or api._parse_datetime(r[2]) or datetime(1970, 1, 1, tzinfo=timezone.utc),
            reverse=True,
        )[:10]
        expected_urls = [r[0] for r in sorted_rows]
        assert [a["source_url"] for a in data["articles"]] == expected_urls

        # Verify last_refresh ends with 'Z' and updated_ago is deterministic (2 hours).
        assert data["last_refresh"].endswith("Z")
        assert data["updated_ago"] == "2 hours ago"
    finally:
        conn.close()
        _teardown_temp_db(db_path, old)

