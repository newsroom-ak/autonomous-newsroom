import os
import shutil
import tempfile
from datetime import datetime

import pytest

# The package path is repo/app, so we need to add the parent to sys.path.
import sys

# Add the ``app`` directory to ``sys.path`` so ``import newsroom`` works.
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from newsroom import db, refresh, feed


@pytest.fixture(scope="function")
def temporary_db():
    """Create a temporary directory with a fresh SQLite file.

    The ``NEWSROOM_DB`` env var is pointed at the temporary file for the duration
    of the test.
    """
    temp_dir = tempfile.mkdtemp()
    db_path = os.path.join(temp_dir, "newsroom.db")
    old_val = os.getenv(db.DB_PATH_ENV)
    os.environ[db.DB_PATH_ENV] = db_path
    try:
        # Ensure the schema is created.
        with db.get_connection() as conn:
            pass
        yield db_path
    finally:
        # Restore original env var.
        if old_val is None:
            del os.environ[db.DB_PATH_ENV]
        else:
            os.environ[db.DB_PATH_ENV] = old_val
        shutil.rmtree(temp_dir, ignore_errors=True)


def test_upsert_article_idempotent(temporary_db):
    # Insert the same article twice; the second call should report ``is_new``
    # as False and not increase the row count.
    article = {
        "source_url": "https://example.com/article1",
        "title": "Example Title",
        "published_at": "2026-01-01T00:00:00Z",
        "summary": None,
        "topic_tag": None,
    }
    _, is_new_first = db.upsert_article(**article)
    _, is_new_second = db.upsert_article(**article)
    assert is_new_first is True
    assert is_new_second is False

    # Verify only one row exists.
    with db.get_connection() as conn:
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM articles")
        count = cur.fetchone()[0]
        assert count == 1


def test_refresh_cli_idempotent(monkeypatch, temporary_db):
    # Mock the feed to return two deterministic articles.
    sample_articles = [
        {
            "source_url": "https://example.com/a",
            "title": "A",
            "published_at": None,
            "fetched_at": datetime.utcnow().isoformat(),
            "summary": None,
            "topic_tag": None,
        },
        {
            "source_url": "https://example.com/b",
            "title": "B",
            "published_at": None,
            "fetched_at": datetime.utcnow().isoformat(),
            "summary": None,
            "topic_tag": None,
        },
    ]

    def fake_fetch_articles(topic=None):
        return sample_articles

    monkeypatch.setattr(feed, "fetch_articles", fake_fetch_articles)

    # First run – should insert two new articles.
    refresh.main()
    with db.get_connection() as conn:
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM articles")
        assert cur.fetchone()[0] == 2
        cur.execute("SELECT COUNT(*) FROM refresh_runs WHERE status = 'success'")
        # One refresh run recorded as success.
        assert cur.fetchone()[0] == 1

    # Second run – articles are duplicates, new count stays 0.
    refresh.main()
    with db.get_connection() as conn:
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM articles")
        assert cur.fetchone()[0] == 2
        cur.execute("SELECT COUNT(*) FROM refresh_runs WHERE status = 'success'")
        # Two successful runs should now exist.
        assert cur.fetchone()[0] == 2

