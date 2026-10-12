"""Database utilities for the Newsroom application.

Only the standard library ``sqlite3`` is used so the code works in the
restricted test environment. The module lazily creates the required tables
the first time a connection is opened.

The schema matches the acceptance criteria from ``TICKET.md``:

* ``articles`` – stores raw article data.
* ``refresh_runs`` – records each refresh execution.

Both tables are created with ``IF NOT EXISTS`` guards. ``articles`` has a
unique constraint on ``source_url`` to guarantee idempotency.
"""

from __future__ import annotations

import os
import sqlite3
import hashlib
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Generator, Tuple, Optional

DB_PATH_ENV = "NEWSROOM_DB"
DEFAULT_DB = "newsroom.db"


def _db_path() -> str:
    """Resolve the SQLite database path from the environment.

    The path is interpreted relative to the current working directory (the
    ``app`` package root when tests invoke the code). ``os.path.abspath``
    normalises it for storage in SQLite.
    """
    return os.getenv(DB_PATH_ENV, DEFAULT_DB)


def _ensure_schema(conn: sqlite3.Connection) -> None:
    """Create tables if they do not already exist.

    ``articles`` has a unique index on ``source_url`` which enforces the
    idempotency requirement.
    """
    cur = conn.cursor()
    # articles table
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS articles (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_url TEXT NOT NULL UNIQUE,
            title TEXT NOT NULL,
            published_at TEXT,
            fetched_at TEXT NOT NULL,
            summary TEXT,
            topic_tag TEXT,
            summary_source TEXT,
            summary_model TEXT,
            content_hash TEXT
        )
        """
    )
    # refresh_runs table
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS refresh_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            started_at TEXT NOT NULL,
            finished_at TEXT,
            articles_fetched INTEGER NOT NULL,
            articles_new INTEGER NOT NULL,
            status TEXT NOT NULL,
            error_message TEXT
        )
        """
    )
    conn.commit()


@contextmanager
def get_connection() -> Generator[sqlite3.Connection, None, None]:
    """Yield a connection with the schema ensured.

    The caller is responsible for committing any changes. The connection is
    closed on exit.
    """
    path = _db_path()
    # Ensure the directory exists for relative paths like ``data/newsroom.db``.
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, detect_types=sqlite3.PARSE_DECLTYPES)
    try:
        _ensure_schema(conn)
        yield conn
    finally:
        conn.close()


def upsert_article(
    *,
    source_url: str,
    title: str,
    published_at: Optional[str] = None,
    fetched_at: Optional[str] = None,
    summary: Optional[str] = None,
    topic_tag: Optional[str] = None,
    summary_source: Optional[str] = None,
    summary_model: Optional[str] = None,
    content_hash: Optional[str] = None,
) -> Tuple[int, bool]:
    """Insert a new article or update an existing one when its title changes.

    Returns ``(article_id, is_new)`` where ``is_new`` is ``True`` when the row was
    freshly inserted **or** when the existing row's ``content_hash`` differed (meaning
    the title changed). In the latter case the summary‑related fields are cleared
    so that the summarisation step can re‑run.
    """
    fetched_at = fetched_at or datetime.utcnow().isoformat()
    # Use a deterministic hash of the title as the content hash if not supplied.
    if content_hash is None:
        content_hash = hashlib.sha256(title.encode()).hexdigest()

    with get_connection() as conn:
        cur = conn.cursor()
        # Attempt insert – respects UNIQUE constraint on source_url.
        cur.execute(
            """
            INSERT OR IGNORE INTO articles (
                source_url, title, published_at, fetched_at, summary, topic_tag,
                summary_source, summary_model, content_hash
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                source_url,
                title,
                published_at,
                fetched_at,
                summary,
                topic_tag,
                summary_source,
                summary_model,
                content_hash,
            ),
        )
        if cur.lastrowid:
            # Fresh insert.
            article_id = cur.lastrowid
            is_new = True
        else:
            # Row exists – check if the title (content_hash) changed.
            cur.execute(
                "SELECT id, content_hash FROM articles WHERE source_url = ?",
                (source_url,),
            )
            row = cur.fetchone()
            article_id = row[0] if row else -1
            existing_hash = row[1] if row else None
            if existing_hash != content_hash:
                # Update title and content_hash, and clear summary related fields.
                cur.execute(
                    """
                    UPDATE articles SET
                        title = ?,
                        published_at = ?,
                        fetched_at = ?,
                        summary = NULL,
                        topic_tag = NULL,
                        summary_source = NULL,
                        summary_model = NULL,
                        content_hash = ?
                    WHERE source_url = ?
                    """,
                    (title, published_at, fetched_at, content_hash, source_url),
                )
                is_new = True
            else:
                is_new = False
        conn.commit()
        return article_id, is_new


def get_unsummarized(limit: int) -> list[tuple[int, str, str]]:
    """Return up to *limit* articles missing a summary.

    The result is ordered by ``fetched_at`` descending (newest first) to satisfy the
    free‑tier limit rule.
    """
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT id, source_url, title FROM articles
            WHERE summary IS NULL
            ORDER BY fetched_at DESC
            LIMIT ?
            """,
            (limit, ),
        )
        return list(cur.fetchall())


def update_summary(
    *,
    article_id: int,
    summary: str,
    topic_tag: str,
    summary_source: str,
    summary_model: str | None = None,
) -> None:
    """Persist a summary and its metadata for *article_id*.

    ``summary_source`` should be either ``"model"`` or ``"fallback"``.
    ``summary_model`` is the name of the model that generated the summary, or ``None``
    for the fallback path.
    """
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            UPDATE articles SET
                summary = ?,
                topic_tag = ?,
                summary_source = ?,
                summary_model = ?
            WHERE id = ?
            """,
            (summary, topic_tag, summary_source, summary_model, article_id),
        )
        conn.commit()


def insert_refresh_run(
    *,
    started_at: str,
    finished_at: Optional[str] = None,
    articles_fetched: int = 0,
    articles_new: int = 0,
    status: str = "success",
    error_message: Optional[str] = None,
) -> int:
    """Insert a refresh run row and return its id."""
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO refresh_runs (
                started_at, finished_at, articles_fetched, articles_new, status, error_message
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                started_at,
                finished_at,
                articles_fetched,
                articles_new,
                status,
                error_message,
            ),
        )
        run_id = cur.lastrowid
        conn.commit()
        return run_id


def update_refresh_run(
    *,
    run_id: int,
    finished_at: str | None = None,
    articles_fetched: int | None = None,
    articles_new: int | None = None,
    status: str | None = None,
    error_message: str | None = None,
) -> None:
    """Update fields of an existing refresh run.

    Only non‑``None`` arguments are written. This helper keeps the refresh
    implementation readable and avoids duplicated ``UPDATE`` SQL strings.
    """
    fields = []
    values = []
    if finished_at is not None:
        fields.append("finished_at = ?")
        values.append(finished_at)
    if articles_fetched is not None:
        fields.append("articles_fetched = ?")
        values.append(articles_fetched)
    if articles_new is not None:
        fields.append("articles_new = ?")
        values.append(articles_new)
    if status is not None:
        fields.append("status = ?")
        values.append(status)
    if error_message is not None:
        fields.append("error_message = ?")
        values.append(error_message)
    if not fields:
        return
    values.append(run_id)
    set_clause = ", ".join(fields)
    sql = f"UPDATE refresh_runs SET {set_clause} WHERE id = ?"
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(sql, tuple(values))
        conn.commit()

