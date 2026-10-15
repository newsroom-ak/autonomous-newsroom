"""Summarization utilities for the Newsroom application.

This module implements the primary/fallback model calls required by **Ticket #25**.
It now asks the model for a **single JSON object** containing a one‑sentence
``summary`` and a ``tag`` selected from a controlled taxonomy. The reply is
validated with **Pydantic v2**; only a usable ``summary`` (≥ 3 alphanumeric
characters) is accepted. An invalid or unparsable reply triggers the fallback
model, and if both models fail we fall back to a deterministic title‑based
summary.

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
# Pydantic model for validation of the model's JSON reply (Ticket #25).
# ---------------------------------------------------------------------------
from pydantic import BaseModel, Field, validator


ALLOWED_TAGS = {
    "research",
    "models",
    "products",
    "tools",
    "hardware",
    "policy",
    "safety",
    "business",
    "open-source",
    "education",
    "general",
}


class SummaryResponse(BaseModel):
    """Schema for the model's JSON output.

    * ``summary`` – a non‑empty string containing at least three alphanumeric
      characters.
    * ``tag`` – optional; when present it is stripped, lower‑cased and must be one
      of :data:`ALLOWED_TAGS`. Validation does **not** reject the whole reply if
      the tag is missing or unknown – callers will substitute ``"general"``.
    """

    summary: str = Field(..., description="One‑sentence summary of the article.")
    tag: str | None = Field(default=None, description="Requested topic tag.")

    @validator("summary")
    def summary_must_have_content(cls, v: str) -> str:
        # Collapse whitespace for the check but keep original spacing.
        if not v or sum(ch.isalnum() for ch in v) < 3:
            raise ValueError("summary must contain at least three alphanumeric characters")
        return v

    @validator("tag", pre=True, always=True)
    def normalize_tag(cls, v: str | None) -> str | None:
        """Normalize the ``tag`` field.

        The ticket specifies that an invalid, missing, or non‑string tag should
        be treated as ``None`` (causing the caller to default to ``"general"``).
        ``v`` may be ``None`` or any type; only a string is accepted.
        """
        if not isinstance(v, str):
            return None
        norm = v.strip().lower()
        return norm or None


def _validate_response(raw: str) -> SummaryResponse:
    """Extract JSON from ``raw`` (optionally wrapped in a Markdown fence) and
    validate it against :class:`SummaryResponse`.

    The ticket requires *strict* JSON parsing: any raw newline characters
    inside the JSON (including within a JSON string) must cause a parsing
    failure, which then triggers the fallback model. Therefore we **do not**
    replace newlines with spaces – we keep the raw text untouched after
    stripping optional Markdown fences.
    """
    # Strip surrounding whitespace first.
    raw = raw.strip()
    # Remove optional Markdown code fences (```json ... ``` or ``` ... ```)
    if raw.startswith("```"):
        # Split first line to detect possible language specifier.
        parts = raw.split("\n", 1)
        if len(parts) == 2:
            # Remove the opening fence line.
            raw = parts[1]
        # Remove trailing fence if present.
        if raw.endswith("```"):
            raw = raw[: -3]
    # Do **not** manipulate newline characters; let the JSON parser raise
    # on any malformed content.
    return SummaryResponse.model_validate_json(raw)

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
    """Return a cleaned, one‑line summary ≤ 160 characters.

    The function removes newlines/tabs, collapses consecutive whitespace, strips
    surrounding quotation marks, and enforces a *whole‑word* truncation. If the
    text exceeds 160 characters the result is cut at the last word boundary
    before the limit and suffixed with an ellipsis ``…`` so the final length does
    not exceed 160.
    """
    # Collapse whitespace and remove surrounding quotes.
    cleaned = " ".join(text.split())
    cleaned = cleaned.strip('"\'')
    if len(cleaned) <= 160:
        return cleaned
    # Find last space before the 160‑char limit.
    cutoff = cleaned.rfind(' ', 0, 160)
    if cutoff == -1:
        # No space found – hard cut.
        cutoff = 160
    trimmed = cleaned[:cutoff].rstrip()
    # Append ellipsis ensuring total length ≤ 160.
    if len(trimmed) + 1 <= 160:
        return f"{trimmed}…"
    # Edge case: trimmed already 160, just return it.
    return trimmed


def _extract_tag(summary: str) -> str:
    """Extract a tag from the model's ``summary`` field.

    The allowed tags are defined in :data:`ALLOWED_TAGS`. If the supplied tag is
    missing, not a string, or not in the whitelist we return ``"general"``.
    """
    # The model reply may already include a ``tag`` field – callers pass it in
    # via the ``SummaryResponse``; however historic code still falls back to the
    # heuristic. We keep the heuristic for backward compatibility.
    match = re.search(r"[A-Za-z]+", summary)
    candidate = match.group(0).lower() if match else "general"
    return candidate if candidate in ALLOWED_TAGS else "general"


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
    """Attempt the primary model, then the fallback, and finally the title.

    The function always returns a 4‑tuple ``(summary, tag, source, model)`` where:

    * ``source`` is ``"model"`` when a model supplied a usable summary, and ``"fallback"``
      when both models failed and the title‑based fallback is used.
    * ``model`` is the name of the model that provided the usable summary, or ``None``
      for the final title fallback.
    """
    # Build a prompt that explicitly asks for JSON output, lists the allowed
    # tags, and includes the article title.
    allowed_tags_str = ", ".join(sorted(ALLOWED_TAGS))
    # Build a concise prompt that **only** instructs the model to return a JSON
    # object. The previous version contained two contradictory sentences that
    # asked the model to include the article title *in the response* and to
    # literally include the word "JSON". Those instructions broke the contract
    # that the model must respond with *only* a JSON payload. We replace them with
    # a single clear sentence: "Return a single valid JSON object."
    prompt = (
        "You are summarizing an article for a news feed. Respond ONLY with a JSON "
        "object (no extra text) containing two keys: \"summary\" and \"tag\". "
        "The summary must be ONE SENTENCE, max 160 characters. The tag must be "
        f"one of: {allowed_tags_str}. Return a single valid JSON object.\n"
        f"Title: {title}\n"
    )

    # Helper to process a raw model reply.
    def _process_reply(raw: str, model_name: str) -> tuple[str, str, str, str] | None:
        """Process a raw model reply.

        Only a **valid JSON** response matching :class:`SummaryResponse` is
        accepted. Any parsing error, validation failure, or missing usable
        summary results in ``None`` so the caller will try the fallback model.
        """
        try:
            resp = _validate_response(raw)
        except Exception:
            # Invalid JSON – trigger fallback.
            return None

        # JSON path – use explicit tag if provided and allowed.
        cleaned = _clean_summary(resp.summary)
        if not _is_usable(cleaned):
            return None
        tag_candidate = resp.tag if isinstance(resp.tag, str) else None
        tag = tag_candidate if tag_candidate in ALLOWED_TAGS else "general"
        return cleaned, tag, "model", model_name

    # Primary model attempt.
    try:
        raw = _call_model(
            _get_primary_url(),
            _get_primary_key(),
            _get_primary_model(),
            prompt,
        )
        result = _process_reply(raw, _get_primary_model())
        if result:
            return result
    except Exception:
        pass

    # Fallback model attempt.
    try:
        raw = _call_model(
            _get_fallback_url(),
            _get_fallback_key(),
            _get_fallback_model(),
            prompt,
        )
        result = _process_reply(raw, _get_fallback_model())
        if result:
            return result
    except Exception:
        pass

    # Title‑based fallback – use the literal first 120 characters of the title
    # as the summary (no ellipsis, no whole‑word truncation). This matches the
    # clarified acceptance criteria that the fallback contract is a literal
    # ``title[:120]`` when *both* model calls fail. The tag is forced to "general",
    # the source is "fallback", and no model name is recorded.
    summary = title[:120]
    return summary, "general", "fallback", None


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

