# Offline test runner for agent-written code.
# Dependencies are fixed here (the allowed list in agents/shared/brief.md) so tests can run
# with --network none. The container never receives a GitHub token or model key.
FROM python:3.12-slim

RUN pip install --no-cache-dir \
        fastapi==0.118.0 \
        uvicorn==0.37.0 \
        jinja2==3.1.6 \
        httpx==0.28.1 \
        feedparser==6.0.12 \
        pytest==8.4.2 \
    && useradd --uid 1000 --create-home runner

USER 1000:1000
WORKDIR /work/app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=/work/app
