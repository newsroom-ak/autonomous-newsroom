"""Feed fetching utilities.

The implementation is deliberately lightweight – it only depends on the
standard library, ``httpx`` and ``feedparser`` which are whitelisted in the
project brief.

Only a single public function ``fetch_articles`` is exported. It returns a list
of dictionaries that match the columns of the ``articles`` table defined in
``db.py``.
"""

from __future__ import annotations

import os
import urllib.parse
from datetime import datetime
from typing import List, Dict, Any

import httpx
import feedparser

# Environment variable name for the topic. The brief defines ``NEWSROOM_TOPIC``
# with a default of ``AI ML``.
TOPIC_ENV = "NEWSROOM_TOPIC"
DEFAULT_TOPIC = "AI ML"

# Google News RSS search endpoint – it accepts a URL‑encoded query string.
GOOGLE_NEWS_RSS = "https://news.google.com/rss/search?q={query}"


def _build_url(topic: str) -> str:
    """Construct the RSS URL for *topic*.

    ``topic`` may contain spaces; they are URL‑encoded before being inserted
    into the base URL.
    """
    query = urllib.parse.quote_plus(topic)
    return GOOGLE_NEWS_RSS.format(query=query)


def _parse_entry(entry: Any) -> Dict[str, Any]:
    """Extract relevant fields from a ``feedparser`` entry.

    The ``published`` attribute may be missing – in that case ``None`` is
    stored. ``published`` values are kept as strings (ISO‑8601 not required by
    the acceptance criteria). ``fetched_at`` is generated here to keep the
    caller simple.
    """
    source_url = entry.get("link")
    title = entry.get("title", "")
    published = entry.get("published", None)
    fetched_at = datetime.utcnow().isoformat()
    return {
        "source_url": source_url,
        "title": title,
        "published_at": published,
        "fetched_at": fetched_at,
        # ``summary`` and ``topic_tag`` are optional for the first ticket – they
        # will be filled by later tickets that call a summariser.
        "summary": None,
        "topic_tag": None,
    }


def fetch_articles(topic: str | None = None) -> List[Dict[str, Any]]:
    """Fetch the RSS feed for *topic* and return a list of article dicts.

    If *topic* is ``None`` the value is taken from the ``NEWSROOM_TOPIC``
    environment variable or falls back to ``DEFAULT_TOPIC``.
    """
    if topic is None:
        topic = os.getenv(TOPIC_ENV, DEFAULT_TOPIC)
    url = _build_url(topic)
    # ``httpx`` is used for its simple API and built‑in timeout handling.
    resp = httpx.get(url, timeout=10.0)
    resp.raise_for_status()
    parsed = feedparser.parse(resp.text)
    articles: List[Dict[str, Any]] = []
    for entry in parsed.entries:
        # Guard against malformed entries – skip if no link.
        if not entry.get("link"):
            continue
        articles.append(_parse_entry(entry))
    return articles

