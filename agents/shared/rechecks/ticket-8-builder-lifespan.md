Reviewer finding (request_changes on fac912d). Edit ONLY `repo/app/newsroom/main.py`.

`main.py` uses the deprecated `@app.on_event("startup")` / `@app.on_event("shutdown")` handlers.
Replace them with a FastAPI lifespan context manager:
- `from contextlib import asynccontextmanager`
- define `async def lifespan(app)` that awaits `scheduler.startup()`, yields, then awaits
  `scheduler.shutdown()`
- create the app with `FastAPI(title="Newsroom API", lifespan=lifespan)` and remove both
  `@app.on_event` handlers.
Keep everything else in main.py unchanged (the `/` route, templates, the API router, imports
it still needs). `newsroom.main.app` must remain the single app object.

Read main.py once, apply one patch, then reply. Nothing else.
