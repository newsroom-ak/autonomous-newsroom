"""Builder-owned tests for the summarizer prompt.

These tests verify that the prompt constructed in ``_summarize_with_fallback``
conforms to the acceptance criteria of ticket #27:

* No contradictory sentences about including the title or the word ``JSON``
  in the model's response.
* The prompt still instructs the model to respond **ONLY** with a JSON object
  containing ``summary`` and ``tag``.
* The allowed tags list is present.
* The article title appears exactly once, labelled ``Title:``.
* The prompt (excluding the title line) is no longer than 455 characters.
* The primary and fallback model calls receive identical prompts.
"""

from __future__ import annotations

import os

import pytest

# Adjust sys.path so ``import newsroom`` works within the test runner.
import sys
import pathlib

sys.path.append(str(pathlib.Path(__file__).parents[2]))  # repo/app directory

from newsroom import summarize


@pytest.fixture(scope="function")
def capture_prompts(monkeypatch):
    """Monkey‑patch ``_call_model`` to capture the prompt passed to each model.

    The fixture returns a list ``prompts`` where each entry is the prompt string
    received for a model call. The first element corresponds to the primary
    model, the second to the fallback (if invoked).
    """

    prompts: list[str] = []

    def fake_call(url: str, key: str, model: str, prompt: str):  # pragma: no cover
        prompts.append(prompt)
        # Simulate primary failure to force a fallback call.
        if model == os.getenv("SUMMARY_PRIMARY_MODEL", "gpt-oss:20b"):
            raise Exception("force fallback")
        # Return a minimal valid JSON response for the fallback model.
        return "{\"summary\": \"ok\", \"tag\": \"research\"}"

    monkeypatch.setattr(summarize, "_call_model", fake_call)
    return prompts


def test_prompt_content_and_length(capture_prompts):
    """Validate the constructed prompt meets ticket #27 criteria.

    The test triggers both primary and fallback calls (primary is forced to fail)
    and then inspects the captured prompt.
    """

    title = "A test article title"
    # Call the internal helper directly to avoid DB side‑effects.
    summarize._summarize_with_fallback(title)

    # Two prompts should have been captured: primary then fallback.
    assert len(capture_prompts) == 2
    primary_prompt, fallback_prompt = capture_prompts

    # Both prompts must be identical.
    assert primary_prompt == fallback_prompt

    # The prompt should contain the JSON‑only instruction.
    assert "Respond ONLY with a JSON object" in primary_prompt
    assert "Return a single valid JSON object" in primary_prompt

    # Contradictory sentences must be absent.
    assert "Include the article title in your prompt for context" not in primary_prompt
    assert "Make sure the JSON is valid and include the word" not in primary_prompt

    # The allowed tags list must be present.
    for allowed in sorted(summarize.ALLOWED_TAGS):
        assert allowed in primary_prompt

    # Title should appear exactly once, prefixed by "Title:".
    title_line = f"Title: {title}\n"
    assert title_line in primary_prompt
    # Count occurrences of the exact title line.
    assert primary_prompt.count(title_line) == 1

    # Compute length without the title line.
    without_title = primary_prompt.replace(title_line, "")
    assert len(without_title) <= 455

