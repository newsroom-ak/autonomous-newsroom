Human review of your ticket #8 tests. Change ONLY these two tests; keep the other three unchanged.
The scheduler itself works (verified in one running event loop and via the app lifespan).

1. `test_scheduler_immediate_refresh_creates_run` sets NEWSROOM_DISABLE_SCHEDULER=1 and then expects a
   refresh. Per the ticket, that flag disables the scheduler entirely (0 runs is correct; your
   `test_scheduler_can_be_disabled` already covers it). Do not set the flag here.

2. Both tests call `asyncio.run(scheduler.startup())` and later a separate `asyncio.run(shutdown())`.
   `startup()` only starts a background task and returns; when the first `asyncio.run` returns,
   its event loop closes and the task is cancelled before it can run. Drive the scheduler inside
   ONE event loop instead, e.g.:

       async def scenario():
           await scheduler.startup()
           await asyncio.sleep(0.05)      # let the background task run
           await scheduler.shutdown()
       asyncio.run(scenario())

   For the immediate-run test, patch `scheduler.sleep` with an async function that waits forever
   (e.g. `await asyncio.Event().wait()`), so exactly 1 refresh happens. For the interval test,
   let the first fake sleep return immediately and the second wait forever (do not raise
   CancelledError yourself), so exactly 2 refreshes happen. Keep the refresh_runs row assertions.

Do not edit anything else. If the scheduler does not meet these, the tests must fail.
