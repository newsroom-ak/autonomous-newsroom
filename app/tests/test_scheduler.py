"""Tests for the background refresh scheduler (Ticket #8).

The scheduler is designed to be test‑friendly: the ``sleep`` function and the
callable that performs the refresh are exposed as module‑level variables that
can be monkey‑patched. The tests verify that:

* An immediate refresh is executed on startup.
* The periodic ``sleep`` is invoked (so the interval logic runs).
* The scheduler can be cleanly shut down without leaving dangling tasks.
* The scheduler respects the ``NEWSROOM_DISABLE_SCHEDULER`` flag.
"""

from __future__ import annotations

import asyncio
import os
import types

import pytest

# Import the scheduler module from the application package.
from newsroom import scheduler


@pytest.mark.asyncio
async def test_scheduler_runs_immediate_refresh(monkeypatch):
    """Scheduler should run a refresh immediately on startup.

    The test patches ``refresh_callable`` to record when it is invoked and
    replaces ``sleep`` with a coroutine that raises ``CancelledError`` after the
    first interval, causing the background loop to terminate.
    """

    called = []

    def fake_refresh():
        called.append(True)

    # Replace the refresh callable with our fake implementation.
    monkeypatch.setattr(scheduler, "refresh_callable", fake_refresh)

    # ``sleep`` will be called with the interval seconds; we capture the value
    # and then raise ``CancelledError`` to stop the loop.
    sleep_calls = []

    async def fake_sleep(seconds: float):  # pragma: no cover – exercised via test.
        sleep_calls.append(seconds)
        # Raising ``CancelledError`` will unwind the background task.
        raise asyncio.CancelledError

    monkeypatch.setattr(scheduler, "sleep", fake_sleep)

    # Ensure the scheduler is enabled for this test.
    monkeypatch.setenv("NEWSROOM_DISABLE_SCHEDULER", "0")

    # Start the scheduler – this creates the background task and runs the
    # immediate refresh before the first sleep.
    await scheduler.startup()

    # Shut it down cleanly; this will cancel the background task (which may have
    # already been cancelled by the fake sleep).
    await scheduler.shutdown()

    # The fake refresh should have been called exactly once.
    assert len(called) == 1, "Refresh callable was not invoked on startup"

    # ``sleep`` should have been called at least once with the configured
    # interval (default 180 minutes → 10800 seconds). The test does not depend on
    # the exact value; we only verify that it was invoked.
    assert sleep_calls, "Scheduler sleep was never called"


@pytest.mark.asyncio
async def test_scheduler_respects_disable_flag(monkeypatch):
    """When ``NEWSROOM_DISABLE_SCHEDULER`` is set, startup should be a no‑op."""
    # Set the env flag to disable.
    monkeypatch.setenv("NEWSROOM_DISABLE_SCHEDULER", "1")

    # Replace the refresh callable with a function that would raise if called.
    def should_not_run():
        raise AssertionError("Refresh should not run when disabled")

    monkeypatch.setattr(scheduler, "refresh_callable", should_not_run)
    # Use a dummy sleep that would also raise if called.
    async def dummy_sleep(_):
        raise AssertionError("Sleep should not be invoked when disabled")

    monkeypatch.setattr(scheduler, "sleep", dummy_sleep)

    await scheduler.startup()
    # No task should be created; internal _task is private but we can check that
    # it is still ``None`` via the module attribute.
    assert getattr(scheduler, "_task", None) is None
    # Shutdown should be harmless.
    await scheduler.shutdown()

