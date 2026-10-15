'''Acceptance tests for Ticket #27: Remove contradictory instructions from the summarizer prompt.

Each test corresponds to one acceptance criterion listed in TICKET.md.
'''

from __future__ import annotations

import os
import sys
import pathlib
import pytest

# Ensure the package root (repo/app) is on the import path so we can import the
# newsroom package as the production code does.
# Add the workspace's ``repo/app`` directory to ``sys.path`` so that the ``newsroom`` package can be imported from this test file.
# ``test_acceptance.py`` lives in ``/workspace/acceptance``; its parent directory (``parents[1]``) is the workspace root (``/workspace``). The application code resides in ``repo/app`` relative to that root.
sys.path.append(str(pathlib.Path(__file__).parents[1] / "repo" / "app"))

from newsroom import summarize

# ---------------------------------------------------------------------------
# Helper fixture to capture the prompt passed to each model call.
# ---------------------------------------------------------------------------
@pytest.fixture
def capture_prompts(monkeypatch):
    """Monkey‑patch :func:`summarize._call_model` to record prompts.

    The fake implementation records the prompt string and optionally raises an
    exception for the primary model to force a fallback call. The return value
    is a minimal valid JSON payload so the fallback processing can continue.
    """
    prompts: list[str] = []

    def fake_call(url: str, key: str, model: str, prompt: str):  # pragma: no cover
        prompts.append(prompt)
        # Force the primary model to fail so both primary and fallback are exercised.
        if model == os.getenv("SUMMARY_PRIMARY_MODEL", "gpt-oss:20b"):
            raise Exception("force fallback")
        # Return a minimal valid JSON that satisfies the schema.
        return "{\"summary\": \"ok\", \"tag\": \"research\"}"

    monkeypatch.setattr(summarize, "_call_model", fake_call)
    return prompts

# ---------------------------------------------------------------------------
# Acceptance criterion 1 – Contradiction removed.
# ---------------------------------------------------------------------------
def test_contradiction_removed(capture_prompts):
    title = "Test article title for contradiction"
    summarize._summarize_with_fallback(title)
    # Two prompts should have been captured (primary then fallback).
    assert len(capture_prompts) == 2
    primary_prompt = capture_prompts[0]
    # The two offending sentences must not appear (case‑insensitive).
    assert "include the article title in your prompt for context".lower() not in primary_prompt.lower()
    assert "make sure the json is valid and include the word".lower() not in primary_prompt.lower()

# ---------------------------------------------------------------------------
# Acceptance criterion 2 – JSON‑only instruction preserved.
# ---------------------------------------------------------------------------
def test_json_only_instruction_preserved(capture_prompts):
    title = "Another test title"
    summarize._summarize_with_fallback(title)
    primary_prompt = capture_prompts[0]
    # Must still contain the explicit JSON‑only directive.
    assert "Respond ONLY with a JSON object" in primary_prompt
    # The new clarification sentence also includes the word JSON.
    assert "Return a single valid JSON object" in primary_prompt

# ---------------------------------------------------------------------------
# Acceptance criterion 3 – Schema and tags preserved.
# ---------------------------------------------------------------------------
def test_schema_and_tags_preserved(capture_prompts):
    title = "Tag preservation test"
    summarize._summarize_with_fallback(title)
    primary_prompt = capture_prompts[0]
    # All allowed tags must be listed in the prompt.
    for tag in sorted(summarize.ALLOWED_TAGS):
        assert tag in primary_prompt
    # The prompt still mentions the required keys.
    assert "summary" in primary_prompt and "tag" in primary_prompt

# ---------------------------------------------------------------------------
# Acceptance criterion 4 – Title context preserved (appears exactly once).
# ---------------------------------------------------------------------------
def test_title_context_preserved(capture_prompts):
    title = "Unique Title 12345"
    summarize._summarize_with_fallback(title)
    primary_prompt = capture_prompts[0]
    title_line = f"Title: {title}\n"
    assert title_line in primary_prompt
    # Ensure the exact line occurs only once.
    assert primary_prompt.count(title_line) == 1

# ---------------------------------------------------------------------------
# Acceptance criterion 5 – No prompt growth (<=455 chars without title line).
# ---------------------------------------------------------------------------
def test_prompt_length_without_title(capture_prompts):
    title = "Length check title"
    summarize._summarize_with_fallback(title)
    primary_prompt = capture_prompts[0]
    title_line = f"Title: {title}\n"
    # Remove the title line before measuring length.
    prompt_without_title = primary_prompt.replace(title_line, "")
    assert len(prompt_without_title) <= 455

# ---------------------------------------------------------------------------
# Acceptance criterion 6 – Same prompt for primary and fallback models.
# ---------------------------------------------------------------------------
def test_prompt_identical_for_both_models(capture_prompts):
    title = "Identical prompt test"
    summarize._summarize_with_fallback(title)
    assert len(capture_prompts) == 2
    primary_prompt, fallback_prompt = capture_prompts
    assert primary_prompt == fallback_prompt
