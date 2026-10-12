"""FastAPI API routes for the Newsroom application.

This module defines the ``/api/articles`` endpoint required by ticket #6.
The implementation follows the acceptance criteria:

* Returns a JSON object ``{articles: [...], last_refresh: <ISO8601 or null>,
  updated_ago: <str or null>}``.
* At most 10 articles, ordered newest first by the *parsed* ``published_at``
  datetime. If ``published_at`` is missing or cannot be parsed, the ``fetched_at``
  timestamp is used as a fallback. The raw strings are **not** sorted
  lexicographically.
* Only articles that have a stored ``summary`` are included.
* Each article in the response contains the fields
  ``source_url, title, published_at, summary, topic_tag, summary_source, summary_model``.
* ``last_refresh`` is the ISO‑8601 UTC timestamp of the most recent successful
  refresh run (status = "success"). The stored value is naïve UTC; we append a
  trailing ``Z`` to indicate UTC.
* ``updated_ago`` is a human‑readable relative time string (e.g. "2 hours ago").
  The computation accepts an injectable ``now`` function for deterministic unit
  testing; by default it uses ``datetime.utcnow``.

The code purposefully avoids heavy dependencies – it uses only the standard
library and the pre‑approved ``fastapi`` package. All settings (database path,
etc.) are read lazily at request time, not at module import, to satisfy the
environment‑variable‑only configuration rule.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Callable, List, Optional, Dict, Any

from fastapi import APIRouter

from . import db

router = APIRouter()


def _parse_datetime(value: Optional[str]) -> Optional[datetime]:
    """Parse a datetime string that may be RFC‑822 or ISO‑8601.

    Returns a timezone‑aware ``datetime`` in UTC, or ``None`` if the input is
    ``None`` or cannot be parsed.
    """
    if not value:
        return None
    # Try ISO‑8601 first (the simplest case for ``datetime.fromisoformat``).
    try:
        dt = datetime.fromisoformat(value)
        # ``fromisoformat`` may produce a naive datetime; treat it as UTC.
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        else:
            dt = dt.astimezone(timezone.utc)
        return dt
    except Exception:
        pass
    # Fallback to RFC‑822 using ``email.utils`` which is in the stdlib.
    try:
        from email.utils import parsedate_to_datetime

        dt = parsedate_to_datetime(value)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        else:
            dt = dt.astimezone(timezone.utc)
        return dt
    except Exception:
        return None


def _human_readable_delta(delta_seconds: float) -> str:
    """Return a concise human‑readable string for *delta_seconds*.

    The format mirrors typical "X unit(s) ago" phrases used by the UI. For
    simplicity we only handle seconds, minutes, hours, and days.
    """
    secs = int(delta_seconds)
    if secs < 60:
        return f"{secs} second{'s' if secs != 1 else ''} ago"
    mins = secs // 60
    if mins < 60:
        return f"{mins} minute{'s' if mins != 1 else ''} ago"
    hours = mins // 60
    if hours < 24:
        return f"{hours} hour{'s' if hours != 1 else ''} ago"
    days = hours // 24
    return f"{days} day{'s' if days != 1 else ''} ago"


def _compute_updated_ago(last_refresh_iso: Optional[str], now_func: Callable[[], datetime]) -> Optional[str]:
    if not last_refresh_iso:
        return None
    last_dt = _parse_datetime(last_refresh_iso)
    if not last_dt:
        return None
    now = now_func().replace(tzinfo=timezone.utc)
    delta = (now - last_dt).total_seconds()
    if delta < 0:
        # Future timestamps are not expected; treat as "0 seconds ago".
        delta = 0
    return _human_readable_delta(delta)


# Helper used by both the JSON API and the server‑rendered homepage.
def _fetch_articles_data(conn) -> tuple[List[Dict[str, Any]], Optional[str], Optional[str]]:
    """Return ``(articles, last_refresh_iso, updated_ago)``.

    * ``articles`` – up to 10 article dicts matching the API contract.
    * ``last_refresh_iso`` – the raw ISO‑8601 timestamp from the latest successful
      refresh, with a trailing ``Z`` to indicate UTC (or ``None`` if no refresh).
    * ``updated_ago`` – a human‑readable relative time string derived from the same
      timestamp, or ``None`` when unavailable.
    """
    cur = conn.cursor()
    cur.execute(
        """
        SELECT source_url, title, published_at, fetched_at, summary,
               topic_tag, summary_source, summary_model
        FROM articles
        WHERE summary IS NOT NULL
        """
    )
    rows = cur.fetchall()

    articles: List[Dict[str, Any]] = []
    for (
        source_url,
        title,
        published_at,
        fetched_at,
        summary,
        topic_tag,
        summary_source,
        summary_model,
    ) in rows:
        parsed = _parse_datetime(published_at) or _parse_datetime(fetched_at)
        articles.append(
            {
                "source_url": source_url,
                "title": title,
                "published_at": published_at,
                "summary": summary,
                "topic_tag": topic_tag,
                "summary_source": summary_source,
                "summary_model": summary_model,
                "_sort_dt": parsed,
            }
        )
    # Newest first.
    articles.sort(key=lambda a: a["_sort_dt"] or datetime(1970, 1, 1, tzinfo=timezone.utc), reverse=True)
    articles = articles[:10]
    for a in articles:
        a.pop("_sort_dt", None)

    # Latest successful refresh.
    cur.execute(
        """
        SELECT finished_at FROM refresh_runs
        WHERE status = 'success' AND finished_at IS NOT NULL
        ORDER BY finished_at DESC LIMIT 1
        """
    )
    last_row = cur.fetchone()
    last_refresh_iso: Optional[str] = last_row[0] if last_row else None

    updated_ago: Optional[str] = None
    if last_refresh_iso:
        updated_ago = _compute_updated_ago(last_refresh_iso, datetime.utcnow)
        last_refresh_iso = f"{last_refresh_iso}Z"

    return articles, last_refresh_iso, updated_ago


@router.get("/api/articles")
def get_articles() -> Dict[str, Any]:
    """Return the article list JSON payload for the public API.

    Delegates to ``_fetch_articles_data`` to avoid code duplication with the
    homepage route.
    """
    with db.get_connection() as conn:
        articles, last_refresh_iso, updated_ago = _fetch_articles_data(conn)
    return {
        "articles": articles,
        "last_refresh": last_refresh_iso,
        "updated_ago": updated_ago,
    }

