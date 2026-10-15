"""New acceptance tests for Ticket #25 – Structured JSON output, strict validation,
controlled tag taxonomy, summary cleaning, and fallback behaviour.

These tests are executed from the repository's ``app`` directory (the test runner
adds that directory to ``sys.path``), so imports use ``from newsroom import ...``.
All external interactions (HTTP calls to the model endpoint) are mocked via
``monkeypatch``. The tests verify that:

* The summarisation pipeline asks for a JSON object containing ``summary`` and
  ``tag`` and mentions the word "JSON" and all allowed tags in the prompt.
* Valid JSON replies – plain or fenced – are parsed correctly.
* Unknown, missing, or mis‑cased tags default to ``"general"``.
* Summaries longer than 160 characters are truncated on a word boundary and
  end with an ellipsis (``…``).
* Multi‑line summaries are collapsed to a single line.
* An invalid primary reply triggers a fallback model call; if the fallback is
  also invalid the title‑based fallback is used.
* The source/model metadata recorded in the DB matches the expectations.
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
    os.path.abspath(os.path.join(os.path.dirname(__file__), "repo", "app"))
)

from newsroom import db, refresh, summarize, feed  # noqa: E402


def _setup_temp_db() -> Tuple[str, sqlite3.Connection, str | None]:
    """Create a temporary SQLite DB and point ``NEWSROOM_DB`` to it.

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
    """Monkey‑patch ``feed.fetch_articles`` to return a single article.
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


def _set_env(monkeypatch):
    """Set deterministic environment variables for the model URLs and names.
    The tests rely on known model identifiers so we can assert on the DB values.
    """
    monkeypatch.setenv("SUMMARY_PRIMARY_URL", "https://primary.test/v1")
    monkeypatch.setenv("SUMMARY_FALLBACK_URL", "https://fallback.test/v1")
    monkeypatch.setenv("SUMMARY_PRIMARY_MODEL", "primary-model-test")
    monkeypatch.setenv("SUMMARY_FALLBACK_MODEL", "fallback-model-test")
    monkeypatch.setenv("SUMMARY_PRIMARY_KEY", "primary-key")
    monkeypatch.setenv("SUMMARY_FALLBACK_KEY", "fallback-key")

def _mock_call_model(monkeypatch, response: str):
    """Patch ``summarize._call_model`` to return the supplied raw response string.
    """
    _set_env(monkeypatch)
    monkeypatch.setattr(
        summarize,
        "_call_model",
        lambda url, key, model, prompt: response,
    )


def _capture_prompt(monkeypatch):
    """Replace ``summarize._call_model`` with a wrapper that records the prompt.
    Returns a dict that will be populated with the last ``prompt`` string.
    """
    captured: dict = {}

    def _wrapper(url, key, model, prompt):
        captured["prompt"] = prompt
        # Return a minimal valid JSON reply so the pipeline proceeds.
        return '{"summary":"OK","tag":"research"}'

    monkeypatch.setattr(summarize, "_call_model", _wrapper)
    return captured


def _assert_db_row(conn, expected_summary, expected_tag, expected_source, expected_model):
    cur = conn.cursor()
    cur.execute(
        "SELECT summary, topic_tag, summary_source, summary_model FROM articles"
    )
    summary, tag, source, model = cur.fetchone()
    assert summary == expected_summary
    assert tag == expected_tag
    assert source == expected_source
    assert model == expected_model


def test_valid_json_reply(monkeypatch):
    db_path, conn, old = _setup_temp_db()
    try:
        _mock_feed_single("Some title")
        _mock_call_model(monkeypatch, '{"summary":"A brief summary.", "tag":"research"}')
        refresh._run_refresh()
        _assert_db_row(conn, "A brief summary.", "research", "model", "primary-model-test")
    finally:
        conn.close()
        _teardown_temp_db(db_path, old)


def test_fenced_json_reply(monkeypatch):
    db_path, conn, old = _setup_temp_db()
    try:
        _mock_feed_single("Title")
        raw = """```json\n{ \"summary\": \"One sentence summary.\", \"tag\": \"models\" }\n```"""
        _mock_call_model(monkeypatch, raw)
        refresh._run_refresh()
        _assert_db_row(conn, "One sentence summary.", "models", "model", "primary-model-test")
    finally:
        conn.close()
        _teardown_temp_db(db_path, old)


def test_unknown_tag(monkeypatch):
    db_path, conn, old = _setup_temp_db()
    try:
        _mock_feed_single("Title")
        _mock_call_model(monkeypatch, '{"summary":"Valid summary.", "tag":"unknown"}')
        refresh._run_refresh()
        _assert_db_row(conn, "Valid summary.", "general", "model", "primary-model-test")
    finally:
        conn.close()
        _teardown_temp_db(db_path, old)


def test_missing_tag(monkeypatch):
    db_path, conn, old = _setup_temp_db()
    try:
        _mock_feed_single("Title")
        _mock_call_model(monkeypatch, '{"summary":"Only summary present."}')
        refresh._run_refresh()
        _assert_db_row(conn, "Only summary present.", "general", "model", "primary-model-test")
    finally:
        conn.close()
        _teardown_temp_db(db_path, old)


def test_mis_cased_tag(monkeypatch):
    db_path, conn, old = _setup_temp_db()
    try:
        _mock_feed_single("Title")
        _mock_call_model(monkeypatch, '{"summary":"Valid.", "tag":"ReSeArCh"}')
        refresh._run_refresh()
        _assert_db_row(conn, "Valid.", "research", "model", "primary-model-test")
    finally:
        conn.close()
        _teardown_temp_db(db_path, old)


def test_summary_truncation_long(monkeypatch):
    db_path, conn, old = _setup_temp_db()
    try:
        long_text = " ".join(f"word{i}" for i in range(200))
        _mock_feed_single("Title")
        _mock_call_model(monkeypatch, f'{{"summary":"{long_text}", "tag":"hardware"}}')
        refresh._run_refresh()
        cur = conn.cursor()
        cur.execute("SELECT summary FROM articles")
        (summary,) = cur.fetchone()
        assert len(summary) <= 160
        assert summary.endswith("…")
        # Ensure we did not cut inside a word.
        assert " " not in summary[-2:]
    finally:
        conn.close()
        _teardown_temp_db(db_path, old)


def test_multiline_summary(monkeypatch):
    db_path, conn, old = _setup_temp_db()
    try:
        ml = "Line1\nLine2\nLine3"
        _mock_feed_single("Title")
        _mock_call_model(monkeypatch, f'{{"summary":"{ml}", "tag":"tools"}}')
        refresh._run_refresh()
        cur = conn.cursor()
        cur.execute("SELECT summary FROM articles")
        (summary,) = cur.fetchone()
        assert "\n" not in summary
        assert summary == "Line1 Line2 Line3"
    finally:
        conn.close()
        _teardown_temp_db(db_path, old)


def test_invalid_primary_fallback(monkeypatch):
    """Primary returns invalid JSON → fallback model supplies usable result."""
    db_path, conn, old = _setup_temp_db()
    try:
        _mock_feed_single("Title")

        def fake_call(url, key, model, prompt):
            if model == summarize._get_primary_model():
                return "not json"
            return '{"summary":"Fallback OK", "tag":"education"}'

        _set_env(monkeypatch)
        monkeypatch.setattr(summarize, "_call_model", fake_call)
        refresh._run_refresh()
        _assert_db_row(conn, "Fallback OK", "education", "model", "fallback-model-test")
    finally:
        conn.close()
        _teardown_temp_db(db_path, old)


def test_both_invalid_uses_title_fallback(monkeypatch):
    """Both primary and fallback replies are invalid → title fallback is stored."""
    db_path, conn, old = _setup_temp_db()
    try:
        title = "A very long title that will be truncated for the fallback summary"
        _mock_feed_single(title)
        _mock_call_model(monkeypatch, "{}")  # empty JSON object – invalid per schema
        # Both models will receive the same stubbed response.
        refresh._run_refresh()
        expected = summarize._clean_summary(title)
        _assert_db_row(conn, expected, "general", "fallback", None)
    finally:
        conn.close()
        _teardown_temp_db(db_path, old)


def test_prompt_includes_allowed_tags_and_json(monkeypatch):
    db_path, conn, old = _setup_temp_db()
    try:
        _mock_feed_single("Sample title")
        captured = _capture_prompt(monkeypatch)
        refresh._run_refresh()
        prompt = captured["prompt"]
        # Must mention the word JSON and include each allowed tag.
        assert "JSON" in prompt
        for tag in summarize.ALLOWED_TAGS:
            assert tag in prompt
        # Title must be present.
        assert "Title: Sample title" in prompt
    finally:
        conn.close()
        _teardown_temp_db(db_path, old)

