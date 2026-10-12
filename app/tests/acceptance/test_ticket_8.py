"""Acceptance tests for ticket #8 – background refresh scheduler.

These tests verify the behaviour introduced in ``app/newsroom/scheduler.py``
without performing any real network requests. The implementation is exercised
through the public ``scheduler.startup``/``scheduler.shutdown`` helpers and the
CLI entry point ``newsroom.refresh._run_refresh``.

The test suite mirrors the style of the existing acceptance tests under
``repo/app/tests/acceptance``: a temporary SQLite database is created for each test
by setting the ``NEWSROOM_DB`` environment variable. Helper functions are used
to initialise the schema and to query the ``refresh_runs`` table.
"""

from __future__ import annotations

import os
import sys
import tempfile
import shutil
import asyncio
from datetime import datetime

import pytest

# Add the repository's ``app`` directory to ``sys.path`` so ``import newsroom`` works.
repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "repo", "app"))
sys.path.append(repo_root)

from newsroom import db, scheduler, refresh  # noqa: E402


def _setup_temp_db():
    """Create a temporary SQLite DB and point ``NEWSROOM_DB`` at it.

    Returns a tuple ``(db_path, old_env)`` where ``old_env`` is the previous value
    of the ``NEWSROOM_DB`` environment variable so the test can restore it.
    """
    temp_dir = tempfile.mkdtemp()
    db_path = os.path.join(temp_dir, "newsroom.db")
    old_env = os.getenv(db.DB_PATH_ENV)
    os.environ[db.DB_PATH_ENV] = db_path
    # Initialise the schema.
    with db.get_connection() as conn:
        db._ensure_schema(conn)
    return db_path, old_env


def _teardown_temp_db(db_path: str, old_env: str | None):
    if old_env is None:
        os.environ.pop(db.DB_PATH_ENV, None)
    else:
        os.environ[db.DB_PATH_ENV] = old_env
    shutil.rmtree(os.path.dirname(db_path), ignore_errors=True)


def _refresh_run_count() -> int:
    """Return the number of rows in ``refresh_runs``.
    """
    with db.get_connection() as conn:
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM refresh_runs")
        return cur.fetchone()[0]


@pytest.fixture(autouse=True)
def _reset_env():
    """Ensure the ``NEWSROOM_DB`` env var does not leak between tests."""
    original = os.getenv(db.DB_PATH_ENV)
    yield
    if original is None:
        os.environ.pop(db.DB_PATH_ENV, None)
    else:
        os.environ[db.DB_PATH_ENV] = original


def test_scheduler_immediate_refresh_creates_run(monkeypatch):
    """FastAPI startup should trigger an immediate refresh run.

    The test disables the background loop (``NEWSROOM_DISABLE_SCHEDULER=1``) to
    keep the process deterministic and verifies that exactly one ``refresh_runs``
    row is inserted.
    """
    db_path, old = _setup_temp_db()
    try:
        # Ensure the scheduler is disabled after the immediate run.
        monkeypatch.setenv("NEWSROOM_DISABLE_SCHEDULER", "1")

        # Use a fake refresh that simply records a row – no network.
        def fake_refresh():
            db.insert_refresh_run(started_at=datetime.utcnow().isoformat(), status="success")

        monkeypatch.setattr(scheduler, "refresh_callable", fake_refresh)

        # Run the startup coroutine.
        asyncio.run(scheduler.startup())
        # Immediate refresh should have inserted one row.
        assert _refresh_run_count() == 1
    finally:
        # Clean up.
        asyncio.run(scheduler.shutdown())
        _teardown_temp_db(db_path, old)


def test_scheduler_interval_triggers_additional_refresh(monkeypatch):
    """After the immediate run, the background loop should invoke the refresh again.

    The ``sleep`` function is patched to return quickly on the first call and then
    raise ``CancelledError`` on the second call, causing the background task to
    exit after performing a second refresh.
    """
    db_path, old = _setup_temp_db()
    try:
        # Ensure scheduler is enabled.
        monkeypatch.delenv("NEWSROOM_DISABLE_SCHEDULER", raising=False)

        call_counter = {"count": 0}

        def fake_refresh():
            call_counter["count"] += 1
            db.insert_refresh_run(started_at=datetime.utcnow().isoformat(), status="success")

        monkeypatch.setattr(scheduler, "refresh_callable", fake_refresh)

        # Patch ``sleep`` to control the loop.
        sleep_calls = {"n": 0}

        async def fake_sleep(seconds: float):
            sleep_calls["n"] += 1
            if sleep_calls["n"] == 1:
                return  # first sleep – allow next iteration quickly
            raise asyncio.CancelledError()  # second sleep – stop the loop

        monkeypatch.setattr(scheduler, "sleep", fake_sleep)

        # Run startup which performs the immediate refresh and starts background.
        asyncio.run(scheduler.startup())
        # Shutdown will cancel any still‑running background task.
        asyncio.run(scheduler.shutdown())

        # Expect two refreshes: immediate + one after the first sleep.
        assert call_counter["count"] == 2
        assert _refresh_run_count() == 2
    finally:
        _teardown_temp_db(db_path, old)


def test_concurrent_refresh_is_skipped(monkeypatch):
    """If a refresh is already running, a scheduled run is skipped.

    The test manually acquires the scheduler lock, then calls ``_run_once`` and
    verifies that the refresh callable is *not* invoked.
    """
    db_path, old = _setup_temp_db()
    try:
        def should_not_run():
            raise AssertionError("Refresh should be skipped when lock is held")

        monkeypatch.setattr(scheduler, "refresh_callable", should_not_run)

        async def inner():
            await scheduler._lock.acquire()
            try:
                await scheduler._run_once()
            finally:
                scheduler._lock.release()

        asyncio.run(inner())
        assert _refresh_run_count() == 0
    finally:
        _teardown_temp_db(db_path, old)


def test_cli_refresh_still_works(monkeypatch):
    """The CLI entry point ``python -m newsroom.refresh`` continues to function.

    The test patches the feed fetcher to return an empty list, runs the refresh
    function directly, and checks that a ``refresh_runs`` row with status
    ``success`` is recorded.
    """
    db_path, old = _setup_temp_db()
    try:
        monkeypatch.setattr(refresh.feed, "fetch_articles", lambda: [])
        refresh._run_refresh()
        with db.get_connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT status FROM refresh_runs")
            rows = cur.fetchall()
        assert len(rows) == 1
        assert rows[0][0] == "success"
    finally:
        _teardown_temp_db(db_path, old)


def test_scheduler_can_be_disabled(monkeypatch):
    """Setting ``NEWSROOM_DISABLE_SCHEDULER=1`` prevents any refresh runs.
    """
    db_path, old = _setup_temp_db()
    try:
        monkeypatch.setenv("NEWSROOM_DISABLE_SCHEDULER", "1")

        def should_not_run():
            raise AssertionError("Scheduler should be disabled")

        monkeypatch.setattr(scheduler, "refresh_callable", should_not_run)
        asyncio.run(scheduler.startup())
        assert _refresh_run_count() == 0
    finally:
        asyncio.run(scheduler.shutdown())
        _teardown_temp_db(db_path, old)

