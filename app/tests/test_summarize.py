"""Tests for the summarization step (ticket #5).

All network traffic is mocked; the tests verify that primary/fallback handling,
the stored metadata, the free‑tier limit, and idempotency behave as specified.
"""

from __future__ import annotations

import os
import shutil
import tempfile
from datetime import datetime
from typing import Tuple

import pytest

# Adjust sys.path so ``import newsroom`` works.
import sys
import pathlib

sys.path.append(str(pathlib.Path(__file__).parents[2]))  # repo/app directory

from newsroom import db, summarize


@pytest.fixture(scope="function")
def temporary_db() -> Tuple[str, str]:
    """Create a temporary SQLite file and point ``NEWSROOM_DB`` to it.

    Returns the path to the DB file and the original env value (or ``None``).
    """
    temp_dir = tempfile.mkdtemp()
    db_path = os.path.join(temp_dir, "newsroom.db")
    old_val = os.getenv(db.DB_PATH_ENV)
    os.environ[db.DB_PATH_ENV] = db_path
    # Ensure schema is created.
    with db.get_connection() as conn:
        pass
    yield db_path, old_val
    # Cleanup.
    if old_val is None:
        os.environ.pop(db.DB_PATH_ENV, None)
    else:
        os.environ[db.DB_PATH_ENV] = old_val
    shutil.rmtree(temp_dir, ignore_errors=True)


def insert_article(source_url: str, title: str) -> int:
    """Helper to insert an article without a summary and return its id."""
    article_id, _ = db.upsert_article(
        source_url=source_url,
        title=title,
        published_at=None,
        fetched_at=datetime.utcnow().isoformat(),
    )
    return article_id


def test_primary_success(monkeypatch, temporary_db):
    _, _ = temporary_db
    # Insert a single article.
    insert_article("https://example.com/1", "Primary success title")

    # Mock the primary model to return a concise summary.
    def fake_call(url, key, model, prompt):  # pragma: no cover
        return "A short summary from primary"

    monkeypatch.setattr(summarize, "_call_model", fake_call)

    summarize.summarize_pending()

    # Verify the article now has a summary and correct metadata.
    with db.get_connection() as conn:
        cur = conn.cursor()
        cur.execute("SELECT summary, topic_tag, summary_source, summary_model FROM articles")
        summary, tag, source, model = cur.fetchone()
        assert summary == "A short summary from primary"
        assert source == "model"
        # Model name should be the primary model env var.
        assert model == os.getenv("SUMMARY_PRIMARY_MODEL", "gpt-oss:20b")
        # Tag extraction is simple – the first word lower‑cased.
        assert tag == "a"


def test_fallback_used_on_primary_error(monkeypatch, temporary_db):
    _, _ = temporary_db
    insert_article("https://example.com/2", "Needs fallback")

    calls = []

    def fake_call(url, key, model, prompt):
        # Record which URL/model is used.
        calls.append((url, model))
        if model == os.getenv("SUMMARY_PRIMARY_MODEL", "gpt-oss:20b"):
            # Simulate primary HTTP 429 error by raising httpx.HTTPStatusError.
            import httpx
            raise httpx.HTTPStatusError("429 Too Many Requests", request=None, response=type("R", (), {"status_code": 429})())
        return "Fallback summary outcome"

    monkeypatch.setattr(summarize, "_call_model", fake_call)

    summarize.summarize_pending()

    # Verify the article now has a summary and correct metadata for a fallback.
    # The ticket specifies that fallback results should be stored with
    # ``summary_source == "model"`` and the fallback model name recorded.
    with db.get_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT summary, summary_source, summary_model FROM articles"
        )
        summary, source, model = cur.fetchone()
        assert summary == "Fallback summary outcome"
        assert source == "model"
        # The fallback model name comes from the environment variable.
        assert model == os.getenv(
            "SUMMARY_FALLBACK_MODEL", "nemotron-3-nano:30b"
        )

    # Ensure both primary and fallback were attempted.
    assert any(call[1] == os.getenv("SUMMARY_PRIMARY_MODEL", "gpt-oss:20b") for call in calls)
    assert any(call[1] == os.getenv("SUMMARY_FALLBACK_MODEL", "nemotron-3-nano:30b") for call in calls)


def test_both_fail_uses_title(monkeypatch, temporary_db):
    _, _ = temporary_db
    title = "Very long title that exceeds the normal summary length but will be truncated when both models fail"
    insert_article("https://example.com/3", title)

    def always_fail(url, key, model, prompt):
        import httpx
        raise httpx.ConnectError("network down")

    monkeypatch.setattr(summarize, "_call_model", always_fail)

    summarize.summarize_pending()

    with db.get_connection() as conn:
        cur = conn.cursor()
        cur.execute("SELECT summary, topic_tag, summary_source, summary_model FROM articles")
        summary, tag, source, model = cur.fetchone()
        # Summary should be truncated title (first 120 chars).
        assert summary == title[:120]
        assert tag == "general"
        assert source == "fallback"
        assert model is None


def test_max_limit_and_idempotent(monkeypatch, temporary_db):
    _, _ = temporary_db
    # Insert 5 articles; set limit env to 3 using monkeypatch so it is restored.
    monkeypatch.setenv("NEWSROOM_MAX_SUMMARIES", "3")
    for i in range(5):
        insert_article(f"https://example.com/{i}", f"Title {i}")

    call_counter = {"count": 0}

    def fake_call(url, key, model, prompt):
        call_counter["count"] += 1
        return f"summary {model}"

    monkeypatch.setattr(summarize, "_call_model", fake_call)

    # First run – only 3 summaries should be generated.
    summarize.summarize_pending()
    assert call_counter["count"] == 3

    # Second run – the remaining 2 articles should be summarized.
    summarize.summarize_pending()
    assert call_counter["count"] == 5

    # Third run – all articles are summarized, so no further calls.
    summarize.summarize_pending()
    assert call_counter["count"] == 5

