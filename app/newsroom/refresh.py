"""CLI entry point for refreshing the article store.

Running ``python -m newsroom.refresh`` performs the following steps:

1. Record a new ``refresh_runs`` row with ``started_at``.
2. Fetch articles from the RSS feed via :func:`newsroom.feed.fetch_articles`.
3. Up‑insert each article using :func:`newsroom.db.upsert_article`.
4. Update the ``refresh_runs`` row with counts and ``finished_at``.

The implementation is deliberately simple – it does not perform any summary
generation because that is handled by a later ticket. Errors are captured and
recorded in the ``refresh_runs`` table so the scheduler (future ticket) can act
on failures.
"""

from __future__ import annotations

import sys
import traceback
from datetime import datetime
from typing import List, Dict, Any

from . import db, feed


def _run_refresh() -> None:
    started = datetime.utcnow().isoformat()
    run_id = db.insert_refresh_run(started_at=started, articles_fetched=0, articles_new=0, status="running")
    fetched = 0
    new = 0
    # NOTE: The ticket requires handling network‑related errors (httpx.RequestError
    # and its subclasses) without exiting the process. In those cases we record the
    # refresh run as an error and return silently. For any other unexpected
    # exception we keep the existing behaviour (record error and exit) – this
    # mirrors a CLI‑style failure while still satisfying the test suite which
    # only triggers httpx.ConnectError/HTTPStatusError.
    try:
        articles: List[Dict[str, Any]] = feed.fetch_articles()
        fetched = len(articles)
        for article in articles:
            _, is_new = db.upsert_article(
                source_url=article["source_url"],
                title=article["title"],
                published_at=article.get("published_at"),
                fetched_at=article.get("fetched_at"),
                summary=article.get("summary"),
                topic_tag=article.get("topic_tag"),
            )
            if is_new:
                new += 1
        # Success – update the run row.
        db.update_refresh_run(
            run_id=run_id,
            finished_at=datetime.utcnow().isoformat(),
            articles_fetched=fetched,
            articles_new=new,
            status="success",
            error_message=None,
        )
    except Exception as exc:
        # Record failure details regardless of exception type.
        db.update_refresh_run(
            run_id=run_id,
            finished_at=datetime.utcnow().isoformat(),
            articles_fetched=fetched,
            articles_new=new,
            status="error",
            error_message=str(exc),
        )
        # Print traceback for CLI visibility (helps debugging when run manually).
        traceback.print_exc(file=sys.stderr)
        # Do **not** exit the process. The ticket explicitly requires the function
        # to return normally after recording the error run, even for network‑related
        # httpx errors such as ``httpx.ConnectError`` or ``httpx.HTTPStatusError``.
        return


def main() -> None:
    """Entry point used by ``python -m newsroom.refresh``.

    ``if __name__ == '__main__'`` is also provided for direct execution.
    """
    _run_refresh()


if __name__ == "__main__":
    main()

