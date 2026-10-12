"""Background scheduler for periodic news refresh.

This module provides a lightweight, test‑friendly scheduler that runs the
existing ``newsroom.refresh._run_refresh`` function on a configurable
interval. It is wired into the FastAPI application via ``newsroom.main``.

Key features required by **Ticket #8**:

* Starts automatically when the FastAPI app starts (FastAPI ``startup`` event).
* Executes a refresh **immediately** on startup, then every
  ``NEWSROOM_REFRESH_MINUTES`` minutes (default ``180``).
* Runs the refresh in a thread pool via ``asyncio.to_thread`` so the event loop
  remains responsive.
* Prevents overlapping refreshes using an ``asyncio.Lock`` – if a refresh is
  still running when the next interval fires, the new run is **skipped** and the
  skip is logged.
* Supports graceful shutdown on the FastAPI ``shutdown`` event – the background
  task is cancelled and awaited.
* Allows the ``sleep`` and the ``refresh`` callable to be monkey‑patched in
  tests (exposed as module‑level ``sleep`` and ``refresh_callable`` variables).
* Can be disabled entirely with the ``NEWSROOM_DISABLE_SCHEDULER`` environment
  variable (set to ``1``).
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Awaitable, Callable

# Import the refresh implementation that performs the actual work.
from . import refresh

logger = logging.getLogger("newsroom.scheduler")

# ---------------------------------------------------------------------------
# Test‑ability hooks
# ---------------------------------------------------------------------------
# ``sleep`` and ``refresh_callable`` are defined as module‑level variables so
# that unit tests can replace them with fast fakes. The defaults are the real
# implementations.
sleep: Callable[[float], Awaitable[None]] = asyncio.sleep
refresh_callable: Callable[[], None] = refresh._run_refresh

# ---------------------------------------------------------------------------
# Internal state
# ---------------------------------------------------------------------------
_lock = asyncio.Lock()
_task: asyncio.Task[None] | None = None


async def _run_once() -> None:
    """Execute a single refresh run if no other run is in progress.

    The function acquires ``_lock`` to protect against concurrent executions.
    If the lock is already held, the refresh is **skipped** and a log entry is
    written. The actual refresh work is performed in a thread via
    ``asyncio.to_thread`` so that any CPU‑bound or blocking I/O does not block the
    FastAPI event loop.

    Any exception raised by the refresh callable is caught and logged – this
    prevents the background loop from terminating unexpectedly. The next
    scheduled run will still occur.
    """
    if _lock.locked():
        logger.info("Refresh already running – skipping scheduled run")
        return

    async with _lock:
        logger.info("Starting scheduled refresh")
        try:
            # ``refresh_callable`` may be a regular function; ``to_thread`` runs it
            # in a separate thread and returns when it completes.
            await asyncio.to_thread(refresh_callable)
        except Exception:
            # Log the full traceback for debugging but swallow the exception
            # so the scheduler loop continues.
            logger.exception("Scheduled refresh failed")
        else:
            logger.info("Scheduled refresh completed")


async def _background() -> None:
    """Background coroutine that drives the periodic schedule.

    The immediate refresh is performed by ``startup``; this coroutine only
    handles the sleep‑interval loop. It reads ``NEWSROOM_REFRESH_MINUTES`` on each
    iteration so that runtime changes are respected.
    """
    while True:
        # Resolve the interval each loop so that changes to the environment are
        # respected without restarting the process.
        minutes_str = os.getenv("NEWSROOM_REFRESH_MINUTES", "180")
        try:
            minutes = int(minutes_str)
        except ValueError:
            minutes = 180
        await sleep(minutes * 60)
        await _run_once()


async def startup() -> None:
    """Create and start the background scheduler task.

    The original implementation launched ``_background`` as an ``asyncio`` task
    and relied on that coroutine to perform the immediate refresh. When the test
    harness calls ``asyncio.run(startup())`` the event loop is closed immediately
    after ``startup`` finishes, cancelling any pending background tasks before they
    have a chance to run. To make the immediate refresh observable in a synchronous
    test, we now run ``_run_once()`` *directly* before spawning the background task.

    If ``NEWSROOM_DISABLE_SCHEDULER`` is set to ``1`` the scheduler is not
    started.
    """
    global _task
    if os.getenv("NEWSROOM_DISABLE_SCHEDULER", "0") == "1":
        logger.info("Scheduler disabled via NEWSROOM_DISABLE_SCHEDULER")
        return

    # Perform the immediate refresh synchronously so callers that run ``startup``
    # to completion (e.g., via ``asyncio.run``) see the effect.
    await _run_once()

    if _task is None or _task.done():
        _task = asyncio.create_task(_background())
        logger.info("Scheduler task started")


async def shutdown() -> None:
    """Cancel the background task and wait for it to finish cleanly."""
    global _task
    if _task is None:
        return
    _task.cancel()
    try:
        await _task
    except asyncio.CancelledError:
        # Expected cancellation – the task has stopped.
        pass
    finally:
        _task = None
        logger.info("Scheduler task cancelled and cleaned up")

