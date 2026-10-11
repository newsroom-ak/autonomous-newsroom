# Product brief: Autonomous Newsroom

Input for the `dispatch` agent. It describes the business need and the engineering
constraints that keep agent output testable. It deliberately does not prescribe the
implementation; breaking it into tickets is the dispatch agent's job.

## Business need

A small public web page that lists recent news on one topic ("latest AI/ML news") and
keeps itself up to date with nobody pressing a button.

Required:

1. A public page listing about 10 recent articles on the topic.
2. Each article shows a one-line summary, a topic tag, and a link to the source.
3. A scheduled job refreshes the list from a public news source that needs no paid key
   (Google News RSS search or the Hacker News API). No personal data.
4. Articles are stored in a database (SQLite).
5. The page shows "updated X ago", based on the last successful refresh.
6. A small JSON API (`GET /api/articles`) returns the same articles and the last refresh time.
7. The refresh runs automatically on a schedule inside the app (no button required): a
   background task started with the web app runs the refresh every
   `NEWSROOM_REFRESH_MINUTES` minutes (default 180), in addition to the CLI entry point.
8. Summaries and tags come from a model. Each article is summarized once and the result is
   stored. The summarizer calls a primary free model and falls back to a second free model
   when the first one answers HTTP 402 or 429. If both fail, the item is still stored with
   a plain fallback summary (e.g. the truncated title) so the page never breaks.

Nice to have, only after the core is done: dedupe near-identical stories, click a tag to
filter, a second source, a page listing the last few refresh runs.

## Engineering constraints (fixed by the human engineer)

- Language: Python 3.12. Web: FastAPI with server-rendered Jinja2 templates.
- All product code lives under `app/`. Python package: `app/newsroom/`. Tests: `app/tests/`.
- Allowed third-party packages (preinstalled in the test runner; do not add others):
  `fastapi`, `uvicorn`, `jinja2`, `httpx`, `feedparser`, `pytest`.
  List the ones you use in `app/requirements.txt`.
- Configuration comes from environment variables only. Never hard-code keys.
  - `NEWSROOM_DB` (SQLite path, default `newsroom.db`)
  - `NEWSROOM_TOPIC` (default `AI ML`)
  - `SUMMARY_PRIMARY_URL`, `SUMMARY_PRIMARY_MODEL`, `SUMMARY_PRIMARY_KEY` (OpenAI-compatible
    chat completions endpoint; default Ollama Cloud `https://ollama.com/v1`)
  - `SUMMARY_FALLBACK_URL`, `SUMMARY_FALLBACK_MODEL`, `SUMMARY_FALLBACK_KEY`
    (default OpenRouter `https://openrouter.ai/api/v1`)
- The refresh job is a CLI entry point: `python -m newsroom.refresh` (run from `app/`) and is
  also called by the in-app scheduler, so it must be idempotent. `NEWSROOM_REFRESH_MINUTES`
  configures the schedule.
- Tests run with `cd app && python -m pytest -q` inside a container with **no network**.
  Tests must mock every HTTP call (feeds and model APIs) and use a temporary database.
- Each ticket must be small enough for one pull request and must state testable
  acceptance criteria.

## Delivery order (business priority)

Start with the foundation that everything else depends on: fetching real articles from the
public feed and storing them in SQLite. Then summaries/tags, then the API and page with
"updated X ago", then the scheduled refresh.
