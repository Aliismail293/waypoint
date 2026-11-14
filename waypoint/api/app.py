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
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from waypoint.agents.runtime import WaypointRuntime
from waypoint.analytics import UsageEvent, UsageStore, new_answer_id, question_event, summarise, usage_store
from waypoint.api.auth import User, authenticate
from waypoint.config import Settings, get_settings
from waypoint.course_page import course_page
from waypoint.graph import build_graph, find_route
from waypoint.history import FILTERS, HistoryStore, SourceRef, Turn, history_store, matches, record, topic_of
from waypoint.mcp.timetable.adapter import SampleFileSource, TimetableAdapter
from waypoint.observability.tracing import hash_user
from waypoint.pack import build_pack, pack_email, pack_pdf
from waypoint.plans import (
    Plan,
    PlanError,
    PlanStore,
    PlanTerm,
    add_course,
    check_codes_exist,
    plan_store,
    review_plan,
    tidy,
)
from waypoint.profile import Profile, ProfileError, ProfileStore, profile_context, profile_store, validate_profile
from waypoint.safety import redact_pii
from waypoint.timetable_check import timetable_view

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


class PlanTermBody(BaseModel):
    term: str = Field(max_length=20)
    courses: list[str] = Field(default_factory=list, max_length=12)


class PlanBody(BaseModel):
    name: str = Field(default="Plan A", min_length=1, max_length=40)
    terms: list[PlanTermBody] = Field(default_factory=list, max_length=14)


class AddCourseBody(BaseModel):
    code: str = Field(max_length=12)
    term: str | None = Field(default=None, max_length=20)


class TimetableBody(BaseModel):
    term: str = Field(max_length=20)
    section_ids: list[str] = Field(default_factory=list, max_length=40)


class PinBody(BaseModel):
    pinned: bool = True


class PackBody(BaseModel):
    plan_id: str | None = Field(default=None, max_length=20)
    include_pinned: bool = True
    include_critic: bool = True
    timetable_term: str | None = Field(default=None, max_length=20)


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
    plans: PlanStore | None = None,
    history: HistoryStore | None = None,
) -> FastAPI:
    settings = settings or get_settings()
    state: dict[str, Any] = {
        "runtime": runtime,
        "usage": usage,
        "profiles": profiles,
        "plans": plans,
        "history": history,
    }
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
        if state["plans"] is None:
            state["plans"] = plan_store(settings)
        if state["history"] is None:
            state["history"] = history_store(settings)
        yield

    app = FastAPI(title="Waypoint", lifespan=lifespan)
    origins = [o.strip() for o in settings.cors_origins.split(",") if o.strip()]
    if origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=origins,
            allow_methods=["GET", "POST", "PUT", "DELETE"],
            allow_headers=["Authorization", "Content-Type"],
        )
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

    def record_turn(
        user: User, answer_id: str, session_id: str, question: str, answer: str, citations: list, category: str
    ) -> None:
        """Keep the answered question in the student's History. Never allowed to break an answer."""
        try:
            turn = Turn(
                id=answer_id,
                session_id=session_id,
                question=redact_pii(question)[0][:2000],
                answer=answer[:6000],
                category=category or "",
                sources=[SourceRef(**c) for c in citations],
            )
            turns = state["history"].get(user.user_id)
            state["history"].put(user.user_id, record(turns, turn))
        except Exception:
            logging.exception("history not recorded")

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
        await run_in_threadpool(state["plans"].delete, user.user_id)
        await run_in_threadpool(state["history"].delete, user.user_id)
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

    # ---- plans, critic report, timetable ----

    async def load_plans(user: User) -> list[Plan]:
        return await run_in_threadpool(state["plans"].get, user.user_id)

    def find_plan(plans: list[Plan], plan_id: str) -> Plan:
        found = next((p for p in plans if p.id == plan_id), None)
        if found is None:
            raise HTTPException(status_code=404, detail="That plan doesn't exist.")
        return found

    async def save_plans(user: User, plans: list[Plan]) -> None:
        await run_in_threadpool(state["plans"].put, user.user_id, plans)

    async def with_review(user: User, plan: Plan) -> dict:
        profile = await load_profile(user)
        rt: WaypointRuntime = state["runtime"]
        assert rt.deps is not None
        review = await run_in_threadpool(review_plan, plan, profile, rt.deps.catalog)
        # Titles for the cards and source rows for the citation badges, so the page needs no further lookups.
        rows = await run_in_threadpool(rt.deps.catalog.run, "list_courses", {})
        wanted = {c for t in plan.terms for c in t.courses}
        titles = {r["code"]: r["title"] for r in rows if r["code"] in wanted}
        chunk_ids = sorted({c for item in review["items"] for c in item["chunk_ids"]})
        sources = await run_in_threadpool(rt.source_rows, chunk_ids)
        return {**plan.model_dump(), "review": review, "titles": titles, "sources": sources}

    @app.get("/api/plans")
    async def list_plans(user: User = Depends(current_user)) -> dict:  # noqa: B008
        plans = await load_plans(user)
        return {"plans": [await with_review(user, p) for p in plans]}

    @app.post("/api/plans")
    async def create_plan(body: PlanBody, user: User = Depends(current_user)) -> dict:  # noqa: B008
        plans = await load_plans(user)
        if len(plans) >= 6:
            raise HTTPException(status_code=422, detail="That's the maximum of 6 plans. Delete one first.")
        try:
            plan = tidy(Plan(name=body.name, terms=[PlanTerm(**t.model_dump()) for t in body.terms]))
            await run_in_threadpool(check_codes_exist, plan, state["runtime"].deps.catalog)
        except PlanError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        await save_plans(user, [*plans, plan])
        return await with_review(user, plan)

    @app.put("/api/plans/{plan_id}")
    async def update_plan(plan_id: str, body: PlanBody, user: User = Depends(current_user)) -> dict:  # noqa: B008
        plans = await load_plans(user)
        old = find_plan(plans, plan_id)
        try:
            new = tidy(
                Plan(
                    id=old.id,
                    name=body.name,
                    terms=[PlanTerm(**t.model_dump()) for t in body.terms],
                    timetable=old.timetable,
                )
            )
            await run_in_threadpool(check_codes_exist, new, state["runtime"].deps.catalog)
        except PlanError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        await save_plans(user, [new if p.id == plan_id else p for p in plans])
        return await with_review(user, new)

    @app.delete("/api/plans/{plan_id}")
    async def delete_plan(plan_id: str, user: User = Depends(current_user)) -> dict:  # noqa: B008
        plans = await load_plans(user)
        find_plan(plans, plan_id)
        await save_plans(user, [p for p in plans if p.id != plan_id])
        return {"ok": True}

    @app.post("/api/plans/{plan_id}/duplicate")
    async def duplicate_plan(plan_id: str, user: User = Depends(current_user)) -> dict:  # noqa: B008
        plans = await load_plans(user)
        old = find_plan(plans, plan_id)
        if len(plans) >= 6:
            raise HTTPException(status_code=422, detail="That's the maximum of 6 plans. Delete one first.")
        letter = chr(ord("A") + len(plans)) if len(plans) < 26 else "Z"
        copy = old.model_copy(deep=True, update={"id": Plan().id, "name": f"Plan {letter}"})
        await save_plans(user, [*plans, copy])
        return await with_review(user, copy)

    @app.post("/api/plans/add-course")
    async def add_to_plan(body: AddCourseBody, plan_id: str = "", user: User = Depends(current_user)) -> dict:  # noqa: B008
        """Chat's "Add MAT224H5 to my plan": into the given plan (or the first, creating Plan A if none)."""
        plans = await load_plans(user)
        profile = await load_profile(user)
        plan = find_plan(plans, plan_id) if plan_id else (plans[0] if plans else Plan())
        try:
            updated = await run_in_threadpool(
                add_course, plan, body.code, body.term, profile, state["runtime"].deps.catalog
            )
        except PlanError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        await save_plans(user, [updated if p.id == updated.id else p for p in plans] if plans else [updated])
        return await with_review(user, updated)

    @app.get("/api/plans/{plan_id}/timetable")
    async def plan_timetable(plan_id: str, term: str, user: User = Depends(current_user)) -> dict:  # noqa: B008
        plan = find_plan(await load_plans(user), plan_id)
        courses = next((t.courses for t in plan.terms if t.term == term), [])
        return await run_in_threadpool(timetable_view, timetable, courses, term, plan.timetable.get(term, []))

    @app.put("/api/plans/{plan_id}/timetable")
    async def choose_sections(plan_id: str, body: TimetableBody, user: User = Depends(current_user)) -> dict:  # noqa: B008
        plans = await load_plans(user)
        plan = find_plan(plans, plan_id)
        courses = next((t.courses for t in plan.terms if t.term == body.term), [])
        view = await run_in_threadpool(timetable_view, timetable, courses, body.term, body.section_ids)
        plan.timetable[body.term] = [c["section_id"] for c in view["chosen"]]
        await save_plans(user, plans)
        return view

    # ---- history and the advisor pack ----

    @app.get("/api/history")
    async def get_history(q: str = "", filter: str = "all", user: User = Depends(current_user)) -> dict:  # noqa: A002, B008
        if filter not in FILTERS:
            raise HTTPException(status_code=422, detail="Unknown filter.")
        turns = await run_in_threadpool(state["history"].get, user.user_id)
        shown = [t for t in turns if matches(t, q, filter)]
        return {
            "turns": [t.model_dump() | {"topic": topic_of(t)} for t in shown],
            "total": len(turns),
            "pinned": sum(1 for t in turns if t.pinned),
        }

    @app.post("/api/history/{turn_id}/pin")
    async def pin_turn(turn_id: str, body: PinBody, user: User = Depends(current_user)) -> dict:  # noqa: B008
        turns = await run_in_threadpool(state["history"].get, user.user_id)
        turn = next((t for t in turns if t.id == turn_id), None)
        if turn is None:
            raise HTTPException(status_code=404, detail="That answer isn't in your history.")
        turn.pinned = body.pinned
        await run_in_threadpool(state["history"].put, user.user_id, turns)
        return {"id": turn_id, "pinned": turn.pinned}

    @app.delete("/api/history/{turn_id}")
    async def delete_turn(turn_id: str, user: User = Depends(current_user)) -> dict:  # noqa: B008
        turns = await run_in_threadpool(state["history"].get, user.user_id)
        await run_in_threadpool(state["history"].put, user.user_id, [t for t in turns if t.id != turn_id])
        return {"ok": True}

    async def gather_pack(user: User, body: PackBody):
        profile = await load_profile(user)
        plans = await load_plans(user)
        plan = None
        if body.plan_id:
            plan = await with_review(user, find_plan(plans, body.plan_id))
        turns = (
            [t for t in await run_in_threadpool(state["history"].get, user.user_id) if t.pinned]
            if body.include_pinned
            else []
        )
        view = None
        if body.timetable_term and body.plan_id:
            chosen = find_plan(plans, body.plan_id)
            courses = next((t.courses for t in chosen.terms if t.term == body.timetable_term), [])
            view = await run_in_threadpool(
                timetable_view, timetable, courses, body.timetable_term, chosen.timetable.get(body.timetable_term, [])
            )
        return build_pack(
            profile=profile.model_dump(), plan=plan, turns=turns, timetable=view, include_critic=body.include_critic
        )

    @app.post("/api/pack/pdf")
    async def pack_pdf_endpoint(body: PackBody, user: User = Depends(current_user)) -> Response:  # noqa: B008
        pack = await gather_pack(user, body)
        data = await run_in_threadpool(pack_pdf, pack)
        return Response(
            data,
            media_type="application/pdf",
            headers={"Content-Disposition": 'attachment; filename="waypoint-advisor-pack.pdf"'},
        )

    @app.post("/api/pack/email")
    async def pack_email_endpoint(body: PackBody, user: User = Depends(current_user)) -> dict:  # noqa: B008
        return {"text": pack_email(await gather_pack(user, body))}

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
        await run_in_threadpool(
            record_turn,
            user,
            answer_id,
            answer.session_id,
            body.message,
            answer.text,
            [c.model_dump() for c in citations],
            str(metrics.get("category") or ""),
        )
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
                    await run_in_threadpool(
                        record_turn,
                        user,
                        answer_id,
                        answer.session_id,
                        body.message,
                        answer.text,
                        citations,
                        str(metrics.get("category") or ""),
                    )
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
