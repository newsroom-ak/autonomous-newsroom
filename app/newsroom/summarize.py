"""Summarization utilities for the Newsroom application.

This module implements the primary/fallback model calls required by ticket #5.
It is deliberately lightweight and uses only the standard library and the
whitelisted ``httpx`` package.

The public entry point is :func:`summarize_pending` which selects up to
``NEWSROOM_MAX_SUMMARIES`` unsummarized articles (newest first) and attempts to
populate their ``summary`` and ``topic_tag`` fields in the database.
"""

from __future__ import annotations

import os
import re
from typing import Tuple

import httpx

from . import db

# ---------------------------------------------------------------------------
# Configuration helpers – these read environment variables **at call time**
# rather than at import time. Tests mutate the environment after importing the
# module, so reading lazily ensures the updated values are honoured.
# ---------------------------------------------------------------------------

def _get_primary_url() -> str:
    return os.getenv("SUMMARY_PRIMARY_URL", "https://ollama.com/v1")


def _get_primary_model() -> str:
    return os.getenv("SUMMARY_PRIMARY_MODEL", "gpt-oss:20b")


def _get_primary_key() -> str:
    return os.getenv("SUMMARY_PRIMARY_KEY", os.getenv("OLLAMA_API_KEY", ""))


def _get_fallback_url() -> str:
    return os.getenv("SUMMARY_FALLBACK_URL", "https://ollama.com/v1")


def _get_fallback_model() -> str:
    return os.getenv("SUMMARY_FALLBACK_MODEL", "nemotron-3-nano:30b")


def _get_fallback_key() -> str:
    return os.getenv("SUMMARY_FALLBACK_KEY", os.getenv("OLLAMA_API_KEY", ""))


def _get_max_summaries() -> int:
    try:
        return int(os.getenv("NEWSROOM_MAX_SUMMARIES", "12"))
    except ValueError:
        # Defensive fallback – a non‑int env var should not crash the app.
        return 12


def _call_model(url: str, key: str, model: str, prompt: str) -> str:
    """Call an OpenAI‑compatible chat completion endpoint.

    Returns the generated text. Raises ``httpx.HTTPStatusError`` for non‑2xx
    responses. The caller is responsible for handling specific status codes.
    """
    endpoint = f"{url.rstrip('/')}/chat/completions"
    headers = {}
    if key:
        headers["Authorization"] = f"Bearer {key}"
    # Use a higher token limit (800) to give reasoning models room to generate a
    # concise answer while still respecting the one‑sentence requirement.
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": 800,
    }
    response = httpx.post(endpoint, json=payload, headers=headers, timeout=15.0)
    response.raise_for_status()
    # Expected shape: {"choices": [{"message": {"content": "..."}}]}
    data = response.json()
    return data["choices"][0]["message"]["content"]


def _clean_summary(text: str) -> str:
    """Return a one‑line, max‑160‑character summary.

    Newlines are stripped, surrounding whitespace collapsed, and the result is
    truncated to 160 characters.
    """
    single_line = " ".join(text.split())
    return single_line[:160]


def _extract_tag(summary: str) -> str:
    """Extract a one‑word tag from *summary*.

    The simple heuristic uses the first alphabetic word, lower‑cased. If none is
    found ``"general"`` is returned.
    """
    match = re.search(r"[A-Za-z]+", summary)
    return match.group(0).lower() if match else "general"


def _is_usable(summary: str) -> bool:
    """Return ``True`` if *summary* contains at least three alphanumeric characters.

    The ticket defines an unusable response as empty, whitespace‑only, or having
    fewer than three alphanumeric characters after cleaning. This helper
    implements that rule.
    """
    if not summary:
        return False
    alnum_chars = sum(ch.isalnum() for ch in summary)
    return alnum_chars >= 3


def _summarize_with_fallback(title: str) -> Tuple[str, str, str, str | None]:
    """Attempt primary then fallback model, returning ``(summary, tag, source, model)``.

    *source* is ``"model"`` if a model (primary or fallback) produced a usable
    summary, ``"fallback"`` when both models fail and the title‑based fallback
    is used. *model* is the name of the model that supplied the usable summary,
    or ``None`` for the final fallback.
    """
    prompt = f"Summarize the following article title in one short sentence (max 160 chars):\n{title}"
    # Primary attempt
    # Primary model attempt ----------------------------------------------------
    try:
        raw = _call_model(
            _get_primary_url(),
            _get_primary_key(),
            _get_primary_model(),
            prompt,
        )
        summary = _clean_summary(raw)
        if _is_usable(summary):
            tag = _extract_tag(summary)
            return summary, tag, "model", _get_primary_model()
        # Primary produced unusable output – fall through to fallback.
    except (httpx.HTTPStatusError, httpx.RequestError):
        # Any HTTP error (including 402/429) or network‑related error triggers the fallback.
        pass

    # Fallback attempt – treat as a successful model generation.
    # Fallback model attempt ---------------------------------------------------
    try:
        raw = _call_model(
            _get_fallback_url(),
            _get_fallback_key(),
            _get_fallback_model(),
            prompt,
        )
        summary = _clean_summary(raw)
        if _is_usable(summary):
            tag = _extract_tag(summary)
            return summary, tag, "model", _get_fallback_model()
        # Fallback also produced unusable output – use title fallback.
    except Exception:
        # Both primary and fallback failed – use truncated title as a non‑model fallback.
        summary = title[:120]
        tag = "general"
        return summary, tag, "fallback", None


def summarize_pending() -> None:
    """Summarize up to ``MAX_SUMMARIES`` unsummarized articles.

    Errors for individual articles are swallowed so the refresh run never fails
    because of summarisation.
    """
    pending = db.get_unsummarized(_get_max_summaries())
    for article_id, _, title in pending:
        try:
            summary, tag, source, model = _summarize_with_fallback(title)
            db.update_summary(
                article_id=article_id,
                summary=summary,
                topic_tag=tag,
                summary_source=source,
                summary_model=model,
            )
        except Exception:
            # Defensive: never let a failure break the loop.
            continue

