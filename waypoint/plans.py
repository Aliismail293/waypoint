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
from waypoint.datastore import DataStore, get_store
from waypoint.ingest.prereq_parser import describe
from waypoint.observability.tracing import hash_user
from waypoint.prereq_eval import evaluate
from waypoint.profile import Profile
from waypoint.tools.catalog import Catalog, normalise_code

COLLECTION = "plans"
MAX_PLANS = 6
MAX_TERMS = 12
MAX_COURSES_PER_TERM = 8
MAX_CREDITS_PER_TERM = 2.5
TERM = re.compile(r"^(\d{4}) (Fall|Winter|Summer)$")
SEASON_ORDER = {"Winter": 0, "Summer": 1, "Fall": 2}
CODE = re.compile(r"^[A-Z]{3}[A-D0-9]\d{2}[HY]\d$")
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


def program_credits(
    catalog: Catalog, program: str, store: DataStore | None = None
) -> tuple[tuple[float, float] | None, list[str]]:
    """(min, max) credits the program requires, from its parsed calendar text; None if it doesn't say."""
    if not program:
        return None, []
    store = store or get_store()
    row = store.program(program)
    if row is None:
        return None, []
    need = row["requirements_tree"]["credits_required"]
    chunk_ids = [f"cal:{_slug(row['area'])}#{_slug(program)}-completion"]
    return ((need["min"], need["max"]) if need else None), chunk_ids


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:60]


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
        tree = (get_store().get(code) or {}).get("prereq_tree")
        if fits and evaluate(tree, have, profile.highschool_done).status != "unmet":
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
#
# Levels: "ok" (a check that passed), "headsup" (a real conflict, amber) and "note" (neutral information).
# A prerequisite the student simply hasn't got yet is a note, never an error: the card's "i" shows the requirement.


def _item(level: str, text: str, chunk_ids: list[str] | None = None, codes: list[str] | None = None) -> dict[str, Any]:
    return {"level": level, "text": text, "chunk_ids": sorted(set(chunk_ids or [])), "codes": codes or []}


def _and(items: list[str]) -> str:
    return " and ".join(items) if len(items) <= 2 else ", ".join(items[:-1]) + f", and {items[-1]}"


def primary_program(profile: Profile) -> str:
    return profile.programs[0] if profile.programs else profile.program


def review_plan(plan: Plan, profile: Profile, catalog: Catalog, store: DataStore | None = None) -> dict[str, Any]:
    """Deterministic critic report. Courses the student has taken or is taking count as already in hand."""
    store = store or get_store()
    done = set(profile.codes("done"))
    in_hand = set(profile.codes())
    planned_in = {code: t.term for t in plan.terms for code in t.courses}
    seen: set[str] = set()
    items: list[dict[str, Any]] = []
    cards: dict[str, dict[str, Any]] = {}
    credits_per_term: dict[str, float] = {}
    planned_credits = 0.0
    reported_pairs: set[tuple[str, str]] = set()
    for t in plan.terms:
        planned_now: set[str] = set()
        term_credits = 0.0
        for code in t.courses:
            course = store.get(code)
            if course is None:
                items.append(
                    _item(
                        "note",
                        f"{code} ({t.term}) isn't in Waypoint's course data, so it can't be checked.",
                        codes=[code],
                    )
                )
                continue
            chunk = [course["chunk_id"]] if course.get("chunk_id") else []
            term_credits += float(course["credits"])
            planned_now.add(code)
            before = in_hand | seen
            tree = course["prereq_tree"]
            now_eval = evaluate(tree, before, profile.highschool_done)
            cards[code] = _card(course, now_eval)
            if code in done:
                items.append(_item("headsup", f"You've already taken {code}; it is in {t.term} anyway.", chunk, [code]))
            if course.get("status") == "discontinued":
                items.append(_item("headsup", f"{code} isn't offered anymore, so it can't be planned.", chunk, [code]))
            if now_eval.status == "unmet":
                everything = (before | set(planned_in)) - {code}
                later = [m for m in now_eval.missing if m in planned_in]
                if later and evaluate(tree, everything, profile.highschool_done).status != "unmet":
                    for early in later:
                        where = "the same term" if planned_in[early] == t.term else planned_in[early]
                        items.append(
                            _item(
                                "headsup",
                                f"{code} is in {t.term} but its prerequisite {early} is in {where}: "
                                f"move {early} to an earlier term.",
                                chunk,
                                [code, early],
                            )
                        )
                else:
                    items.append(
                        _item(
                            "note",
                            f"{code} asks for {_and(now_eval.missing)}, which isn't in your taken or "
                            "planned courses yet.",
                            chunk,
                            [code, *now_eval.missing],
                        )
                    )
            for other in course["exclusion_codes"]:
                pair = tuple(sorted((code, other)))
                if other != code and (other in in_hand or other in planned_in) and pair not in reported_pairs:
                    reported_pairs.add((pair[0], pair[1]))
                    items.append(
                        _item(
                            "headsup",
                            f"{code} and {other} exclude each other: you can't take both for credit.",
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
            "items": [_item("note", "This plan has no courses yet. Add some, or ask Pip to draft a plan in chat.")],
            "heads_up": [],
            "notes": [],
            "cards": {},
            "credits": None,
            "plan_text": "",
        }
    for term, c in credits_per_term.items():
        if c > MAX_CREDITS_PER_TERM:
            items.append(
                _item(
                    "headsup", f"{term} has {c:g} credits, over the {MAX_CREDITS_PER_TERM:g}-credit full course load."
                )
            )
    chosen = set(profile.programs) | ({profile.program} if profile.program else set())
    for rule_list in (store.rules.get(code, []) for code in sorted(chosen)):
        for rule in rule_list:
            if rule["rule_type"] == "program_exclusion" and set(rule["params"]["programs"]) <= chosen:
                items.append(_item("headsup", rule["source_text"]))
    counted = {c for t in plan.terms for c in t.courses} | done
    total = sum(float((store.get(c) or {}).get("credits") or 0) for c in counted)
    required, program_chunks = program_credits(catalog, primary_program(profile), store)
    if required is not None:
        low, high = required
        label = f"{low:g}" if low == high else f"{low:g}-{high:g}"
        verb = "is below" if total < low else "meets"
        items.append(
            _item(
                "note" if total < low else "ok",
                f"{total:g} credits counted (taken plus planned) {verb} the {label} {primary_program(profile)} needs.",
                program_chunks,
            )
        )
    if not any(i["level"] == "headsup" for i in items):
        busiest = max(credits_per_term.values())
        items.insert(
            0,
            _item(
                "ok",
                f"No heads-ups: nothing is out of order and no term is over {MAX_CREDITS_PER_TERM:g} credits "
                f"(busiest: {busiest:g}).",
            ),
        )
    heads_up = [i for i in items if i["level"] == "headsup"]
    notes = [i for i in items if i["level"] == "note"]
    return {
        "status": "headsup" if heads_up else "notes" if notes else "ok",
        "items": items,
        "heads_up": heads_up,
        "notes": notes,
        "cards": cards,
        "credits": {"counted": total, "planned": planned_credits, "required": list(required) if required else None},
        "plan_text": plan_text(plan),
    }


def _card(course: dict[str, Any], ev: Any) -> dict[str, Any]:
    """What the course card's "i" popover shows: the requirement in plain words and what can't be checked."""
    lines = [ev.text]
    if course.get("coreq_tree"):
        lines.append(f"Corequisite: {describe(course['coreq_tree'])}")
    if course.get("recommended_preparation"):
        lines.append(f"Recommended preparation: {course['recommended_preparation']}")
    if ev.unchecked:
        lines.append(f"Waypoint can't check: {'; '.join(ev.unchecked)}.")
    if course.get("enrolment_notes"):
        lines.append(f"Enrolment: {course['enrolment_notes']}")
    return {"title": course["title"], "lines": lines, "calendar_url": course.get("calendar_url", "")}


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
