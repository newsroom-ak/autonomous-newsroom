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
import shutil
from typing import Tuple

import pytest

# Add the repository's ``app`` directory to ``sys.path`` so that ``import newsroom`` works.
sys.path.append(
    os.path.abspath(os.path.join(os.path.dirname(__file__), "../repo", "app"))
)

from newsroom import db, refresh, summarize, feed  # noqa: E402

# Preserve the original ``fetch_articles`` implementation to restore after each test and avoid leaking state.
_original_fetch_articles = feed.fetch_articles


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
    # Restore any monkey‑patched ``feed.fetch_articles`` to its original implementation.
    feed.fetch_articles = _original_fetch_articles
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
                            {"message": {"content": "{\"summary\": \"Line one.\\nLine two.\", \"tag\": \"research\"}"}}
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



# ---------------------------------------------------------------------------
# Restored acceptance tests (HUMAN-ASSISTED restoration, not OpenClaw Tester output).
#
# These three tests come from the OpenClaw Tester's earlier version of this file
# (commit 540007b). They were lost when the Tester rewrote the file in commit
# 3f63f0f, and the Hermes Reviewer requested them back. The human engineer restored
# them with Claude Code. Differences from 540007b: the four small helpers below are
# copied unchanged; the prompt test lists the allowed tags from the ticket literally
# (it no longer depends on an implementation constant) and only requires the article
# title to appear in the prompt.
# ---------------------------------------------------------------------------

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


# The eleven tags allowed by Issue #25.
_ISSUE_25_TAGS = (
    "research", "models", "products", "tools", "hardware", "policy",
    "safety", "business", "open-source", "education", "general",
)


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


def test_prompt_includes_allowed_tags_and_json(monkeypatch):
    db_path, conn, old = _setup_temp_db()
    try:
        _mock_feed_single("Sample title")
        captured = _capture_prompt(monkeypatch)
        refresh._run_refresh()
        prompt = captured["prompt"]
        # Issue #25: the prompt must mention JSON, list every allowed tag and include the title.
        assert "JSON" in prompt
        for tag in _ISSUE_25_TAGS:
            assert tag in prompt
        assert "Sample title" in prompt
    finally:
        conn.close()
        _teardown_temp_db(db_path, old)
