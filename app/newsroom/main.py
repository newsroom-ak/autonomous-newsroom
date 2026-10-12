"""FastAPI application entry point for the Newsroom service.

The module creates a reusable ``app`` object that includes the API router
defined in :pymod:`newsroom.api`.  This satisfies ticket #6 which requires the
router to be importable by other tickets (e.g., adding a homepage or a scheduler).

All configuration (e.g., database path) is read lazily by the individual route
handlers, so importing this module does **not** perform any I/O.
"""

from __future__ import annotations

from fastapi import FastAPI

from .api import router as api_router

# Reusable FastAPI app instance.
app = FastAPI(title="Newsroom API")
app.include_router(api_router)

