"""Waypoint HTTP API + chat UI (FastAPI). Runs on Cloud Run; locally: `uv run uvicorn waypoint.api.app:app --port 8080`.

Endpoints
- GET  /                 chat UI (static/index.html)
- GET  /api/config       public config for the UI (OAuth client id, advisor link)
- POST /api/chat         ask a question (requires a Google ID token)
- GET  /healthz          liveness
"""

import logging
import time
from collections import defaultdict
from contextlib import asynccontextmanager
from datetime import date
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from waypoint.agents.runtime import WaypointRuntime
from waypoint.api.auth import User, authenticate
from waypoint.config import Settings, get_settings

logging.basicConfig(level=logging.INFO, format="%(message)s")
STATIC = Path(__file__).parent / "static"
MAX_MESSAGE_CHARS = 2000


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=MAX_MESSAGE_CHARS)
    session_id: str | None = Field(default=None, max_length=100)


class Citation(BaseModel):
    chunk_id: str
    heading: str
    url: str


class ChatResponse(BaseModel):
    answer: str
    citations: list[Citation]
    session_id: str
    metrics: dict[str, Any]


class DailyLimiter:
    """Per-user daily request cap (in memory, per instance — a cost guard, not a security boundary)."""

    def __init__(self, limit: int) -> None:
        self.limit = limit
        self._counts: dict[tuple[str, date], int] = defaultdict(int)

    def check(self, user_id: str) -> None:
        key = (user_id, date.today())
        if self._counts[key] >= self.limit:
            raise HTTPException(status_code=429, detail="Daily question limit reached. Please come back tomorrow.")
        self._counts[key] += 1


def create_app(settings: Settings | None = None, runtime: WaypointRuntime | None = None) -> FastAPI:
    settings = settings or get_settings()
    state: dict[str, Any] = {"runtime": runtime}
    limiter = DailyLimiter(settings.max_requests_per_user_per_day)

    @asynccontextmanager
    async def lifespan(_: FastAPI):  # build the agent tree once per container, not per request
        if state["runtime"] is None:
            state["runtime"] = WaypointRuntime.from_settings(settings)
        yield

    app = FastAPI(title="Waypoint", lifespan=lifespan)
    app.mount("/static", StaticFiles(directory=STATIC), name="static")

    def current_user(request: Request) -> User:
        return authenticate(request, settings)

    @app.get("/")
    def index() -> FileResponse:
        return FileResponse(STATIC / "index.html")

    @app.get("/healthz")
    def healthz() -> dict:
        return {"ok": True}

    @app.get("/api/config")
    def config() -> dict:
        return {"google_client_id": settings.google_client_id, "advisor_url": settings.advisor_url}

    @app.post("/api/chat", response_model=ChatResponse)
    async def chat(body: ChatRequest, user: User = Depends(current_user)) -> ChatResponse:  # noqa: B008
        limiter.check(user.user_id)
        rt: WaypointRuntime = state["runtime"]
        started = time.perf_counter()
        answer = await rt.ask(user.user_id, body.message, body.session_id)
        citations = []
        if answer.citations and rt.deps is not None:
            found = {c.chunk_id: c for c in rt.deps.retriever.store.get(answer.citations)}
            citations = [
                Citation(chunk_id=cid, heading=found[cid].heading, url=found[cid].source_url)
                for cid in answer.citations
                if cid in found
            ]
        metrics = answer.metrics() | {"server_ms": round((time.perf_counter() - started) * 1000, 1)}
        return ChatResponse(answer=answer.text, citations=citations, session_id=answer.session_id, metrics=metrics)

    return app


app = create_app()
