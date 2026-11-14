"""Waypoint HTTP API + chat UI (FastAPI). Runs on Cloud Run; locally: `uv run uvicorn waypoint.api.app:app --port 8080`.

Endpoints
- GET  /                 chat UI (static/index.html)
- GET  /privacy, /terms  privacy policy and terms of use (public, no sign-in)
- GET  /api/config       public config for the UI (OAuth client id, advisor link)
- POST /api/chat         ask a question (requires a Google ID token)
- POST /api/feedback     thumbs up/down on an answer
- POST /api/escalate     the student chose "ask an advisor" (returns the advisor link)
- GET  /api/stats        usage numbers (operators only)
- GET  /healthz          liveness
"""

import json
import logging
import time
from collections import defaultdict
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import date
from pathlib import Path
from typing import Any, Literal

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from waypoint.agents.runtime import WaypointRuntime
from waypoint.analytics import UsageEvent, UsageStore, new_answer_id, question_event, summarise, usage_store
from waypoint.api.auth import User, authenticate
from waypoint.config import Settings, get_settings
from waypoint.course_page import course_page
from waypoint.graph import build_graph, find_route
from waypoint.mcp.timetable.adapter import SampleFileSource, TimetableAdapter
from waypoint.observability.tracing import hash_user
from waypoint.profile import Profile, ProfileError, ProfileStore, profile_context, profile_store, validate_profile

logging.basicConfig(level=logging.INFO, format="%(message)s")
STATIC = Path(__file__).parent / "static"
MAX_MESSAGE_CHARS = 2000


def sse(event: str, data: Any) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


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
    answer_id: str
    metrics: dict[str, Any]


class FeedbackRequest(BaseModel):
    session_id: str = Field(max_length=100)
    answer_id: str = Field(max_length=64)
    rating: Literal[1, -1]


class EscalateRequest(BaseModel):
    session_id: str = Field(max_length=100)
    answer_id: str = Field(default="", max_length=64)


class SourceRequest(BaseModel):
    chunk_id: str = Field(pattern=r"^cal:[a-z0-9-]+#[a-z0-9-]+$", max_length=200)
    claim: str = Field(default="", max_length=400)  # the answer sentence that cites this chunk (not the question)


class SourceMismatchRequest(BaseModel):
    chunk_id: str = Field(pattern=r"^cal:[a-z0-9-]+#[a-z0-9-]+$", max_length=200)
    session_id: str = Field(default="", max_length=100)
    answer_id: str = Field(default="", max_length=64)


class ProfileRequest(BaseModel):
    courses: list[dict[str, Any]] = Field(default_factory=list, max_length=200)
    program: str = Field(default="", max_length=60)
    grad_year: int | None = None
    onboarded: bool = True


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


def create_app(
    settings: Settings | None = None,
    runtime: WaypointRuntime | None = None,
    usage: UsageStore | None = None,
    profiles: ProfileStore | None = None,
) -> FastAPI:
    settings = settings or get_settings()
    state: dict[str, Any] = {"runtime": runtime, "usage": usage, "profiles": profiles}
    timetable = TimetableAdapter(SampleFileSource())  # synthetic sample data, labelled wherever it is shown
    limiter = DailyLimiter(settings.max_requests_per_user_per_day)

    @asynccontextmanager
    async def lifespan(_: FastAPI):  # build the agent tree once per container, not per request
        if state["runtime"] is None:
            state["runtime"] = WaypointRuntime.from_settings(settings)
        if state["usage"] is None:
            state["usage"] = usage_store(settings)
        if state["profiles"] is None:
            state["profiles"] = profile_store(settings)
        yield

    app = FastAPI(title="Waypoint", lifespan=lifespan)
    app.mount("/static", StaticFiles(directory=STATIC), name="static")

    def current_user(request: Request) -> User:
        return authenticate(request, settings)

    @app.get("/")
    def index() -> FileResponse:
        return FileResponse(STATIC / "index.html")

    @app.get("/privacy")
    def privacy() -> FileResponse:
        return FileResponse(STATIC / "privacy.html")

    @app.get("/terms")
    def terms() -> FileResponse:
        return FileResponse(STATIC / "terms.html")

    @app.get("/healthz")
    def healthz() -> dict:
        return {"ok": True}

    async def load_profile(user: User) -> Profile:
        return await run_in_threadpool(state["profiles"].get, user.user_id) or Profile()

    async def load_context(user: User) -> str:
        """The signed-in student's own profile as agent context. Per request and per user: nothing is shared."""
        try:
            return profile_context(await load_profile(user))
        except Exception:  # a profile outage must not stop answers; they just aren't personalised
            logging.exception("profile not loaded")
            return ""

    @app.get("/api/config")
    def config() -> dict:
        return {
            "google_client_id": settings.google_client_id,
            "advisor_url": settings.advisor_url,
            # Local development and the browser smoke test run without Google sign-in; never on Cloud Run.
            "dev_mode": settings.dev_skip_auth and not settings.on_cloud_run,
        }

    @app.get("/api/profile")
    async def get_profile(user: User = Depends(current_user)) -> dict:  # noqa: B008
        return (await load_profile(user)).model_dump()

    @app.put("/api/profile")
    async def put_profile(body: ProfileRequest, user: User = Depends(current_user)) -> dict:  # noqa: B008
        catalog = state["runtime"].deps.catalog
        try:
            profile = await run_in_threadpool(validate_profile, body.model_dump(), catalog)
        except ProfileError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        await run_in_threadpool(state["profiles"].put, user.user_id, profile)
        return profile.model_dump()

    @app.delete("/api/profile")
    async def delete_profile(user: User = Depends(current_user)) -> dict:  # noqa: B008
        await run_in_threadpool(state["profiles"].delete, user.user_id)
        return {"ok": True}

    @app.get("/api/courses")
    async def course_search(q: str = "", user: User = Depends(current_user)) -> dict:  # noqa: B008
        """Autocomplete for the profile editor: codes or title words, from the catalog."""
        rows = await run_in_threadpool(state["runtime"].deps.catalog.run, "list_courses", {})
        needle = q.strip().lower().replace(" ", "")
        if not needle:
            return {"courses": []}
        hits = [r for r in rows if needle in r["code"].lower() or needle in r["title"].lower().replace(" ", "")]
        return {"courses": [{"code": r["code"], "title": r["title"]} for r in hits[:8]]}

    @app.get("/api/programs")
    async def programs(user: User = Depends(current_user)) -> dict:  # noqa: B008
        return {"programs": await run_in_threadpool(state["runtime"].deps.catalog.run, "list_programs", {})}

    @app.get("/api/course/{code}")
    async def course(code: str, user: User = Depends(current_user)) -> dict:  # noqa: B008
        profile = await load_profile(user)
        page = await run_in_threadpool(course_page, state["runtime"].deps.catalog, timetable, profile, code)
        if page is None:
            raise HTTPException(status_code=404, detail="That course isn't in Waypoint's data yet.")
        return page

    @app.get("/api/map")
    async def course_map(user: User = Depends(current_user)) -> dict:  # noqa: B008
        profile = await load_profile(user)
        graph = await run_in_threadpool(build_graph, state["runtime"].deps.catalog, profile)
        nodes = sorted(graph.nodes.values(), key=lambda n: n["code"])
        return {"nodes": nodes, "edges": graph.edges}

    @app.get("/api/route/{code}")
    async def course_route(code: str, user: User = Depends(current_user)) -> dict:  # noqa: B008
        profile = await load_profile(user)
        graph = await run_in_threadpool(build_graph, state["runtime"].deps.catalog, profile)
        return find_route(graph, code, profile)

    @app.post("/api/source")
    async def source(body: SourceRequest, user: User = Depends(current_user)) -> dict:  # noqa: B008
        detail = await run_in_threadpool(state["runtime"].source_detail, body.chunk_id, body.claim)
        if detail is None:
            raise HTTPException(status_code=404, detail="That source isn't in Waypoint's data.")
        return detail

    @app.post("/api/source-feedback")
    def source_feedback(body: SourceMismatchRequest, user: User = Depends(current_user)) -> dict:  # noqa: B008
        record_usage(
            UsageEvent(
                type="source_mismatch",
                user=hash_user(user.user_id),
                session_id=body.session_id,
                answer_id=body.answer_id,
                chunk_id=body.chunk_id,
            )
        )
        return {"ok": True}

    @app.post("/api/chat", response_model=ChatResponse)
    async def chat(body: ChatRequest, user: User = Depends(current_user)) -> ChatResponse:  # noqa: B008
        limiter.check(user.user_id)
        rt: WaypointRuntime = state["runtime"]
        started = time.perf_counter()
        context = await load_context(user)
        answer = await rt.ask(user.user_id, body.message, body.session_id, context)
        citations = [Citation(**c) for c in rt.citation_sources(answer.citations)]
        metrics = answer.metrics() | {"server_ms": round((time.perf_counter() - started) * 1000, 1)}
        answer_id = new_answer_id()
        # Firestore's client is blocking; keep it off the event loop.
        await run_in_threadpool(record_usage, question_event(user.user_id, answer.session_id, answer_id, metrics))
        return ChatResponse(
            answer=answer.text,
            citations=citations,
            session_id=answer.session_id,
            answer_id=answer_id,
            metrics=metrics,
        )

    @app.post("/api/chat/stream")
    async def chat_stream(body: ChatRequest, user: User = Depends(current_user)) -> StreamingResponse:  # noqa: B008
        """Server-Sent Events (DESIGN_SPEC section 6): status, sources, token, final, done, or error."""
        limiter.check(user.user_id)
        rt: WaypointRuntime = state["runtime"]

        async def events() -> AsyncIterator[str]:
            context = await load_context(user)
            async for kind, data in rt.ask_stream(user.user_id, body.message, body.session_id, context):
                if kind == "final":
                    answer = data
                    metrics = answer.metrics()
                    answer_id = new_answer_id()
                    await run_in_threadpool(
                        record_usage, question_event(user.user_id, answer.session_id, answer_id, metrics)
                    )
                    citations = [Citation(**c).model_dump() for c in rt.citation_sources(answer.citations)]
                    # `final` carries the checked text (invalid citations removed, PII redacted): the UI swaps it in.
                    yield sse(
                        "final",
                        {
                            "answer": answer.text,
                            "citations": citations,
                            "session_id": answer.session_id,
                            "answer_id": answer_id,
                            "metrics": metrics,
                        },
                    )
                    yield sse(
                        "done",
                        {
                            "ttft_ms": metrics["ttft_ms"],
                            "total_ms": metrics["total_ms"],
                            "model_calls": metrics["model_calls"],
                            "route": metrics["route"],
                            "category": metrics["category"],
                        },
                    )
                else:
                    yield sse(kind, data)

        # No buffering anywhere on the way: Cloud Run and nginx-style proxies honour these headers.
        return StreamingResponse(
            events(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no", "Connection": "keep-alive"},
        )

    def record_usage(event: UsageEvent) -> None:
        try:
            state["usage"].add(event)
        except Exception:  # analytics must never break an answer
            logging.exception("usage event not recorded")

    @app.post("/api/feedback")
    def feedback(body: FeedbackRequest, user: User = Depends(current_user)) -> dict:  # noqa: B008
        record_usage(
            UsageEvent(
                type="feedback",
                user=hash_user(user.user_id),
                session_id=body.session_id,
                answer_id=body.answer_id,
                rating=body.rating,
            )
        )
        return {"ok": True}

    @app.post("/api/escalate")
    def escalate(body: EscalateRequest, user: User = Depends(current_user)) -> dict:  # noqa: B008
        record_usage(
            UsageEvent(
                type="escalation", user=hash_user(user.user_id), session_id=body.session_id, answer_id=body.answer_id
            )
        )
        return {"advisor_url": settings.advisor_url}

    @app.get("/api/stats")
    def stats(user: User = Depends(current_user)) -> dict:  # noqa: B008
        operators = {e.strip().lower() for e in settings.operator_emails.split(",") if e.strip()}
        if user.email.lower() not in operators:
            raise HTTPException(status_code=403, detail="Operators only.")
        return summarise(state["usage"].all())

    return app


app = create_app()
