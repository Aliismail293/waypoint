"""Degree plans: terms of courses per student, and the deterministic review that backs the critic report.

Every check here is plain code (prerequisite order, exclusions, credit load, credit total). The model never decides
whether a plan is valid; it can only be asked to explain these results in chat. Each review item carries the
calendar chunk ids that back it, so the UI can open them in the Source reader.
"""

import re
import uuid
from typing import Any, Protocol

from pydantic import BaseModel, Field

from waypoint.config import Settings
from waypoint.observability.tracing import hash_user
from waypoint.profile import Profile
from waypoint.tools.catalog import Catalog, normalise_code
from waypoint.tools.prereqs import check_prerequisites

COLLECTION = "plans"
MAX_PLANS = 6
MAX_TERMS = 12
MAX_COURSES_PER_TERM = 8
MAX_CREDITS_PER_TERM = 2.5
TERM = re.compile(r"^(\d{4}) (Fall|Winter|Summer)$")
SEASON_ORDER = {"Winter": 0, "Summer": 1, "Fall": 2}
CODE = re.compile(r"^[A-Z]{3}\d{3}[HY]\d$")
CREDITS_REQUIRED = re.compile(r"(\d+(?:\.\d+)?)\s*(?:-|–|to)?\s*(\d+(?:\.\d+)?)?\s*credits are required", re.I)


class PlanTerm(BaseModel):
    term: str
    courses: list[str] = Field(default_factory=list, max_length=MAX_COURSES_PER_TERM)


class Plan(BaseModel):
    id: str = Field(default_factory=lambda: uuid.uuid4().hex[:10])
    name: str = Field(default="Plan A", max_length=40)
    terms: list[PlanTerm] = Field(default_factory=list, max_length=MAX_TERMS)
    # Chosen section ids per term for the Timetable builder: {"2027 Winter": ["MAT224H5-20271-LEC0101", ...]}
    timetable: dict[str, list[str]] = Field(default_factory=dict)


class PlanError(ValueError):
    """The change can't be applied; the message is safe to show to the student."""


def term_key(term: str) -> tuple[int, int]:
    match = TERM.match(term)
    if not match:
        raise PlanError(f"{term!r} isn't a term like '2026 Fall' or '2027 Winter'.")
    return int(match.group(1)), SEASON_ORDER[match.group(2)]


def next_term(term: str) -> str:
    year, season = TERM.match(term).groups()  # type: ignore[union-attr]
    return {"Fall": f"{int(year) + 1} Winter", "Winter": f"{year} Fall", "Summer": f"{year} Fall"}[season]


def tidy(plan: Plan) -> Plan:
    """Chronological terms, no duplicate terms, codes upper-cased, no repeated course across terms."""
    merged: dict[str, list[str]] = {}
    seen: set[str] = set()
    for t in sorted(plan.terms, key=lambda t: term_key(t.term)):  # the earliest term keeps a repeated course
        for raw in t.courses:
            code = normalise_code(raw)
            if not CODE.match(code):
                raise PlanError(f"{raw[:20]!r} isn't a course code like MAT224H5.")
            if code not in seen:
                seen.add(code)
                merged.setdefault(t.term, []).append(code)
        merged.setdefault(t.term, [])
    terms = [PlanTerm(term=k, courses=merged[k]) for k in sorted(merged, key=term_key)]
    return plan.model_copy(update={"terms": terms})


def check_codes_exist(plan: Plan, catalog: Catalog) -> None:
    for t in plan.terms:
        for code in t.courses:
            if not catalog.run("get_course", {"code": code}):
                raise PlanError(f"{code} isn't in Waypoint's course data yet.")


def plan_text(plan: Plan) -> str:
    return "; ".join(f"{t.term}: {', '.join(t.courses)}" for t in plan.terms if t.courses)


def _course(catalog: Catalog, code: str) -> dict[str, Any] | None:
    rows = [r for r in catalog.run("get_course", {"code": code}) if r["code"] == code]
    return rows[0] if rows else None


def program_credits(catalog: Catalog, program: str) -> tuple[tuple[float, float] | None, list[str]]:
    """(min, max) credits the program requires, parsed from its calendar text; None if the text doesn't say."""
    if not program:
        return None, []
    rows = catalog.run("get_program", {"code": program, "pattern": "%\x00%"})
    if not rows:
        return None, []
    match = CREDITS_REQUIRED.search(rows[0].get("completion_requirements") or "")
    chunk_ids = list(rows[0].get("chunk_ids") or [])
    if not match:
        return None, chunk_ids
    low = float(match.group(1))
    return (low, float(match.group(2) or low)), chunk_ids


def suggest_term(plan: Plan, code: str, profile: Profile, catalog: Catalog) -> str:
    """The earliest term where `code`'s prerequisites are met by completed courses plus earlier terms and the
    credit load stays under the cap; a new term after the last one if no existing term fits."""
    course = _course(catalog, code)
    if course is None:
        raise PlanError(f"{code} isn't in Waypoint's course data yet.")
    have = set(profile.codes())
    for t in plan.terms:
        credits = sum(float((_course(catalog, c) or {}).get("credits") or 0.5) for c in t.courses)
        fits = credits + float(course["credits"]) <= MAX_CREDITS_PER_TERM and len(t.courses) < MAX_COURSES_PER_TERM
        if fits and check_prerequisites(course.get("prerequisites") or "", sorted(have)).status != "unmet":
            return t.term
        have |= set(t.courses)
    return next_term(plan.terms[-1].term) if plan.terms else "2026 Fall"


def add_course(plan: Plan, code: str, term: str | None, profile: Profile, catalog: Catalog) -> Plan:
    code = normalise_code(code)
    if not CODE.match(code) or _course(catalog, code) is None:
        raise PlanError(f"{code} isn't in Waypoint's course data yet.")
    if any(code in t.courses for t in plan.terms):
        raise PlanError(f"{code} is already in this plan.")
    chosen = term or suggest_term(plan, code, profile, catalog)
    terms = [t.model_copy(deep=True) for t in plan.terms]
    target = next((t for t in terms if t.term == chosen), None)
    if target is None:
        target = PlanTerm(term=chosen)
        terms.append(target)
    target.courses.append(code)
    return tidy(plan.model_copy(update={"terms": terms}))


# ---- the review (critic report) ----


def _item(level: str, text: str, chunk_ids: list[str] | None = None, codes: list[str] | None = None) -> dict[str, Any]:
    return {"level": level, "text": text, "chunk_ids": sorted(set(chunk_ids or [])), "codes": codes or []}


def review_plan(plan: Plan, profile: Profile, catalog: Catalog) -> dict[str, Any]:
    """Deterministic critic report. Courses the student has taken or is taking count as already in hand."""
    done = set(profile.codes("done"))
    in_hand = set(profile.codes())
    seen: set[str] = set()
    items: list[dict[str, Any]] = []
    prereq_chunks: list[str] = []
    credits_per_term: dict[str, float] = {}
    planned_credits = 0.0
    order_ok = True
    for t in plan.terms:
        planned_now: set[str] = set()
        term_credits = 0.0
        for code in t.courses:
            course = _course(catalog, code)
            if course is None:
                items.append(
                    _item(
                        "error",
                        f"{code} ({t.term}) isn't in Waypoint's course data, so it can't be checked.",
                        codes=[code],
                    )
                )
                continue
            chunk = [course["chunk_id"]] if course.get("chunk_id") else []
            term_credits += float(course["credits"])
            planned_now.add(code)
            if code in done:
                items.append(_item("warn", f"You've already taken {code}; it is in {t.term} anyway.", chunk, [code]))
            check = check_prerequisites(course.get("prerequisites") or "", sorted(in_hand | seen))
            if course.get("prerequisites"):
                prereq_chunks += chunk
            if check.status == "unmet":
                order_ok = False
                needs = ", ".join(check.missing)
                items.append(
                    _item(
                        "error",
                        f"{code} in {t.term} needs {needs} first. Calendar: {check.text}",
                        chunk,
                        [code, *check.missing],
                    )
                )
            elif check.status == "unclear":
                items.append(
                    _item(
                        "warn",
                        f"Confirm {code}'s prerequisite yourself (grades or program rules): {check.text}",
                        chunk,
                        [code],
                    )
                )
            for other in course.get("exclusion_codes") or []:
                if other in in_hand or other in seen or other in planned_now:
                    items.append(
                        _item(
                            "error",
                            f"{code} is an exclusion of {other}: you can't take both for credit.",
                            chunk,
                            [code, other],
                        )
                    )
        credits_per_term[t.term] = term_credits
        planned_credits += term_credits
        seen |= planned_now
    if not any(t.courses for t in plan.terms):
        return {
            "status": "empty",
            "items": [_item("warn", "This plan has no courses yet. Add some, or ask Pip to draft a plan in chat.")],
            "credits": None,
            "plan_text": "",
        }
    if order_ok and not any(i["level"] == "error" for i in items):
        items.insert(
            0, _item("ok", "Every planned course comes after the prerequisites Waypoint can check.", prereq_chunks)
        )
    heavy = {term: c for term, c in credits_per_term.items() if c > MAX_CREDITS_PER_TERM}
    for term, c in heavy.items():
        items.append(_item("error", f"{term} has {c:g} credits, over the {MAX_CREDITS_PER_TERM:g}-credit full load."))
    if not heavy:
        busiest = max(credits_per_term.values())
        items.append(
            _item("ok", f"No term is over the {MAX_CREDITS_PER_TERM:g}-credit full load (busiest: {busiest:g}).")
        )

    counted = {c for t in plan.terms for c in t.courses} | done
    total = sum(float((_course(catalog, c) or {}).get("credits") or 0) for c in counted)
    required, program_chunks = program_credits(catalog, profile.program)
    if required is not None:
        low, high = required
        label = f"{low:g}" if low == high else f"{low:g}–{high:g}"
        if total < low:
            items.append(
                _item(
                    "warn",
                    f"{total:g} credits counted so far (taken plus planned). "
                    f"{profile.program} needs {label}, so add more terms.",
                    program_chunks,
                )
            )
        else:
            items.append(
                _item(
                    "ok",
                    f"{total:g} credits counted (taken plus planned) meets the {label} {profile.program} needs.",
                    program_chunks,
                )
            )
    else:
        items.append(_item("warn", "Set your program in your profile to see credits against its requirement."))
    status = (
        "errors"
        if any(i["level"] == "error" for i in items)
        else "notes"
        if any(i["level"] == "warn" for i in items)
        else "ok"
    )
    return {
        "status": status,
        "items": items,
        "credits": {"counted": total, "planned": planned_credits, "required": list(required) if required else None},
        "plan_text": plan_text(plan),
    }


# ---- stores ----


class PlanStore(Protocol):
    def get(self, user_id: str) -> list[Plan]: ...

    def put(self, user_id: str, plans: list[Plan]) -> None: ...

    def delete(self, user_id: str) -> None: ...


class MemoryPlanStore:
    def __init__(self) -> None:
        self._docs: dict[str, list[dict[str, Any]]] = {}

    def get(self, user_id: str) -> list[Plan]:
        return [Plan(**p) for p in self._docs.get(hash_user(user_id), [])]

    def put(self, user_id: str, plans: list[Plan]) -> None:
        self._docs[hash_user(user_id)] = [p.model_dump() for p in plans]

    def delete(self, user_id: str) -> None:
        self._docs.pop(hash_user(user_id), None)


class FirestorePlanStore:
    def __init__(self, settings: Settings) -> None:
        from google.cloud import firestore

        self._collection = firestore.Client(project=settings.gcp_project).collection(COLLECTION)

    def get(self, user_id: str) -> list[Plan]:
        snap = self._collection.document(hash_user(user_id)).get()
        doc = snap.to_dict() if snap.exists else None
        return [Plan(**p) for p in (doc or {}).get("plans", [])]

    def put(self, user_id: str, plans: list[Plan]) -> None:
        self._collection.document(hash_user(user_id)).set({"plans": [p.model_dump() for p in plans]})

    def delete(self, user_id: str) -> None:
        self._collection.document(hash_user(user_id)).delete()


def plan_store(settings: Settings) -> PlanStore:
    return FirestorePlanStore(settings) if settings.sessions == "firestore" else MemoryPlanStore()
