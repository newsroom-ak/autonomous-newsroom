"""FastAPI application entry point for the Newsroom service.

This module creates a reusable ``app`` object that includes the API router from
``newsroom.api`` **and** the public homepage route required by ticket #7.

All configuration (e.g., database path) is read lazily by the individual route
handlers, so importing this module does **not** perform any I/O.
"""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from .api import router as api_router, _fetch_articles_data
from . import db

# Resolve the template directory relative to the package (not cwd).
# The template directory is ``newsroom/templates`` relative to the ``app``
# package root (the current working directory when the test suite runs).
templates = Jinja2Templates(directory="newsroom/templates")

# Reusable FastAPI app instance.
app = FastAPI(title="Newsroom API")
app.include_router(api_router)


@app.get("/", response_class=HTMLResponse)
def homepage(request: Request) -> HTMLResponse:
    """Render the public homepage with a list of recent articles.

    The underlying data fetching logic is shared with the JSON API via
    ``newsroom.api._fetch_articles_data`` to avoid duplicated SQL.
    """
    with db.get_connection() as conn:
        articles, _last_refresh_iso, updated_ago = _fetch_articles_data(conn)

    context = {
        "request": request,
        "articles": articles,
        "updated_ago": updated_ago,
        "has_articles": bool(articles),
    }
    return templates.TemplateResponse("index.html", context)

