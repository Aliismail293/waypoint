"""Fast-path router: classifies each question before any agent runs, on a small, fast model.

    simple_lookup  one course or one rule          -> policy_agent or course_data_agent, one specialist
    timetable      sections / meeting times        -> timetable_agent
    planning       multi-term plan toward a goal   -> planner (draft + critic), no orchestrator re-write
    unsure         anything else, or a failure      -> the full orchestrator (the Phase 1/2 path)

A wrong "fast" route costs answer quality, a wrong "full" route only costs time, so the router must be
confident to go fast: every failure mode (timeout, bad JSON, unknown label) falls back to `unsure`.
Offline (WAYPOINT_LLM=mock) a keyword heuristic stands in for the model so tests and CI are deterministic.
"""

import json
import logging
import re
import time
from dataclasses import dataclass

from google.genai import types

from waypoint.config import Settings

logger = logging.getLogger(__name__)

CATEGORIES = ("simple_lookup", "timetable", "planning")
SPECIALISTS = {"policy": "policy_agent", "course": "course_data_agent"}

ROUTER_PROMPT = """Classify a University of Toronto Mississauga student's question for routing.
Reply with JSON: {"route": ..., "specialist": ...}.
route:
- "simple_lookup": about ONE course (prerequisites, exclusions, credits, what it unlocks, whether a course the
  student took meets a prerequisite) or ONE rule/policy/program requirement that the calendar states.
- "timetable": asks for sections, meeting times, days, rooms or schedule fit of courses in a term.
- "planning": asks for a plan, a term-by-term schedule, what to take to finish a program or graduate by a date.
- "unsure": spans several of these, needs earlier conversation to understand, is off-topic, or you are not sure.
specialist (only for simple_lookup, otherwise "none"): "course" for facts about a specific course code or the
course catalog, "policy" for program requirements and academic rules/policies."""

ROUTER_SCHEMA = types.Schema(
    type=types.Type.OBJECT,
    properties={
        "route": types.Schema(type=types.Type.STRING, enum=[*CATEGORIES, "unsure"]),
        "specialist": types.Schema(type=types.Type.STRING, enum=["course", "policy", "none"]),
    },
    required=["route", "specialist"],
)


@dataclass
class RouteDecision:
    category: str  # simple_lookup | timetable | planning | unsure
    specialist: str  # agent name for simple_lookup ("policy_agent" / "course_data_agent"), else ""
    ms: float = 0.0
    model_calls: int = 0
    source: str = "heuristic"  # heuristic | model | fallback

    @property
    def fast(self) -> bool:
        return self.category != "unsure"


UNSURE = RouteDecision("unsure", "", source="fallback")

_PLANNING = re.compile(
    r"\bplan\b|term-by-term|map out|each term|\bgraduat\w* (?:with|by|in)\b|\bfinish (?:my|the)\b.*\b(?:in|by)\b"
    r"|\bby 20\d\d\b|what (?:should|do) i take",
    re.I,
)
_TIMETABLE = re.compile(
    r"\bsections?\b|timetable|meeting times?|\b(?:mon|tues?|wednes|thurs?|fri)(?:day)?s?\b|afternoons?|mornings?"
    r"|evenings?|\brooms?\b",
    re.I,
)
_COURSE_CODE = re.compile(r"\b[A-Z]{3}\d{3}(?:[HY]5)?\b", re.I)
_PROGRAM = re.compile(
    r"\bER[A-Z]{3}\d{4}\b|\b(?:major|minor|specialist|program|department)\b|requirement|policy|minimum|cgpa|gpa"
    r"|deadline|petition|probation|good standing|\bwho (?:leads|runs|chairs)\b|graduat",
    re.I,
)
_COURSE_WORDS = re.compile(
    r"prerequisite|prereq|exclusion|corequisite|\bcredits?\b|\bunlocks?\b|breadth|contact hours|identifier"
    r"|\b(?:class|course|subject)\b",
    re.I,
)
_MULTI = re.compile(r"\b(?:and also|as well as|compare|both)\b", re.I)


def heuristic_route(text: str) -> RouteDecision:
    """Keyword stand-in for the model (offline mode and a sanity floor for tests). Conservative by design."""
    if _MULTI.search(text):
        return RouteDecision("unsure", "")
    if _PLANNING.search(text):
        return RouteDecision("planning", "")
    if _TIMETABLE.search(text):
        return RouteDecision("timetable", "")
    if _COURSE_CODE.search(text):
        return RouteDecision("simple_lookup", SPECIALISTS["course"])
    if _PROGRAM.search(text):
        return RouteDecision("simple_lookup", SPECIALISTS["policy"])
    if _COURSE_WORDS.search(text):
        return RouteDecision("simple_lookup", SPECIALISTS["course"])
    return RouteDecision("unsure", "")


def parse_decision(raw: str) -> RouteDecision:
    """Model JSON -> decision. Anything unexpected is `unsure` (the full path)."""
    try:
        data = json.loads(raw)
        category = str(data["route"])
        specialist = str(data.get("specialist", ""))
    except (ValueError, KeyError, TypeError):
        return UNSURE
    if category not in CATEGORIES:
        return UNSURE
    if category == "simple_lookup":
        if specialist not in SPECIALISTS:
            return UNSURE
        return RouteDecision(category, SPECIALISTS[specialist], source="model")
    return RouteDecision(category, "", source="model")


async def classify(text: str, settings: Settings) -> RouteDecision:
    """Route one question. Never raises."""
    started = time.perf_counter()
    if settings.router != "on":
        return RouteDecision("unsure", "", source="fallback")
    if settings.llm != "vertex":
        decision = heuristic_route(text)
        decision.ms = (time.perf_counter() - started) * 1000
        return decision
    try:
        import asyncio

        from google import genai

        client = genai.Client(vertexai=True, project=settings.gcp_project, location=settings.model_location)
        response = await asyncio.wait_for(
            client.aio.models.generate_content(
                model=settings.router_model,
                contents=text,
                config=types.GenerateContentConfig(
                    system_instruction=ROUTER_PROMPT,
                    temperature=0.0,
                    max_output_tokens=64,
                    response_mime_type="application/json",
                    response_schema=ROUTER_SCHEMA,
                    thinking_config=types.ThinkingConfig(thinking_level=types.ThinkingLevel.MINIMAL),
                ),
            ),
            timeout=settings.router_timeout_s,
        )
        decision = parse_decision(response.text or "")
        decision.model_calls = 1
    except Exception:  # timeout, quota, malformed: the full path is always safe
        logger.warning("router failed; using the full path", exc_info=True)
        decision = RouteDecision("unsure", "", model_calls=1, source="fallback")
    decision.ms = (time.perf_counter() - started) * 1000
    return decision
