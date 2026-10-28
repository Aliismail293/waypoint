# Build context (made by deploy.sh): fde-kit/ and fdekit-demo/ side by side, because the demo depends on
# fde-kit by path, exactly as a client project would before fde-kit is on PyPI.
FROM python:3.12-slim
COPY --from=ghcr.io/astral-sh/uv:0.13.6 /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy PYTHONUNBUFFERED=1
WORKDIR /app
COPY fde-kit/ fde-kit/
COPY fdekit-demo/ fdekit-demo/
WORKDIR /app/fdekit-demo
RUN uv sync --frozen --no-dev
RUN useradd --create-home app && chown -R app /app
USER app
ENV PATH="/app/fdekit-demo/.venv/bin:$PATH" PORT=8080
CMD ["sh", "-c", "uvicorn parks_demo.app:app --host 0.0.0.0 --port ${PORT}"]
