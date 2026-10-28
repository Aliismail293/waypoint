"""DEMO web app: sign in with Google, ask about Parks Canada attendance. `uvicorn parks_demo.app:app --port 8080`."""

import json
import logging
from collections import defaultdict
from contextlib import asynccontextmanager
from datetime import date
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse
from google.auth.transport import requests as google_requests
from google.oauth2 import id_token
from pydantic import BaseModel, Field

from parks_demo.agent import Assistant
from parks_demo.config import Settings, get_settings

logging.basicConfig(level=logging.INFO, format="%(message)s")
STATIC = Path(__file__).parent / "static"
logger = logging.getLogger("parks_demo")


class Ask(BaseModel):
    question: str = Field(min_length=1, max_length=1000)


def verify(request: Request, settings: Settings) -> str:
    """Google ID token from the Sign in with Google button -> the account's `sub`. Fails closed."""
    if not settings.google_client_id:
        raise HTTPException(status_code=503, detail="Sign-in isn't configured on this server.")
    header = request.headers.get("Authorization", "")
    if not header.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Sign in with Google first.")
    try:
        claims = id_token.verify_oauth2_token(
            header.removeprefix("Bearer "), google_requests.Request(), audience=settings.google_client_id
        )
    except ValueError as exc:
        raise HTTPException(status_code=401, detail="Invalid sign-in token.") from exc
    if not claims.get("email_verified"):
        raise HTTPException(status_code=401, detail="Email not verified.")
    return str(claims["sub"])


def create_app(settings: Settings | None = None, assistant: Assistant | None = None) -> FastAPI:
    settings = settings or get_settings()
    state: dict[str, Any] = {"assistant": assistant}
    counts: dict[tuple[str, date], int] = defaultdict(int)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        if state["assistant"] is None:
            state["assistant"] = Assistant(settings)
        yield

    app = FastAPI(title="Parks Canada visits (fde-kit demo)", lifespan=lifespan)

    @app.get("/")
    def index() -> FileResponse:
        return FileResponse(STATIC / "index.html")

    @app.get("/healthz")
    def healthz() -> dict:
        return {"ok": True, "demo": True}

    @app.get("/api/config")
    def config() -> dict:
        return {"google_client_id": settings.google_client_id}

    @app.post("/api/ask")
    async def ask(body: Ask, request: Request) -> dict:
        user = verify(request, settings)
        key = (user, date.today())
        if counts[key] >= settings.max_requests_per_user_per_day:
            raise HTTPException(status_code=429, detail="Daily demo limit reached.")
        counts[key] += 1
        reply = await state["assistant"].ask(user, body.question)
        logger.info(json.dumps({"message": "parks.request", **reply.metrics}))  # one structured line per question
        return {"answer": reply.text, "metrics": reply.metrics}

    return app


app = create_app()
