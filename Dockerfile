# Waypoint API on Cloud Run. Built by Cloud Build (`gcloud run deploy --source .`), no local Docker needed.
FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:0.9.5 /uv /bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy PYTHONUNBUFFERED=1

WORKDIR /app
# Dependencies first (cached layer), then the app. Only runtime deps: no dev or eval groups.
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --no-install-project
COPY waypoint ./waypoint
COPY data/corpus ./data/corpus
RUN uv sync --frozen --no-dev

RUN useradd --create-home waypoint
USER waypoint
ENV PATH="/app/.venv/bin:$PATH" PORT=8080
CMD ["sh", "-c", "uvicorn waypoint.api.app:app --host 0.0.0.0 --port ${PORT}"]
