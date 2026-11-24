"""The fast planner: plans built and checked in code from the parsed calendar data, and how the runtime streams them."""

import json
from datetime import date
from pathlib import Path

import pytest

from evals.plan_validity import extract_plan
from waypoint.agents.runtime import WaypointRuntime
from waypoint.config import Settings
from waypoint.datastore import get_store
from waypoint.fastplan import CODE, _elective, build_plan, match_program, parse_request, plan_markdown
from waypoint.prereq_eval import evaluate
from waypoint.tools.catalog import InMemoryCatalog
from waypoint.tools.plan import validate_plan_text

TODAY = date(2026, 11, 24)
QUESTIONS = [
    json.loads(line)
    for line in (Path(__file__).parents[1] / "evals" / "planning_questions.jsonl").read_text().splitlines()
    if line
]
OFFLINE = dict(llm="mock", embeddings="mock", store="memory", catalog="memory", sessions="memory")


@pytest.fixture(scope="module")
def store():
    return get_store()


@pytest.mark.parametrize(
    "text,code",
    [
        ("Plan the Mathematics minor", "ERMIN2511"),
        ("Plan the rest of the Mathematics major (Mathematics stream)", "ERMAJ2511"),
        ("Can you plan my Applied Mathematics major courses?", "ERMAJ2512"),
        ("Plan my second year toward the Mathematical Sciences Specialist", "ERSPE2511"),
        ("Plan my Computer Science major", ""),  # not in the data: never matched to a math program
        ("Make a plan to finish a mathematics degree by 2029", ""),  # no program type named
    ],
)
def test_match_program(store, text, code):
    assert match_program(text, store) == code


def test_parse_request_reads_completed_wanted_terms_and_load(store):
    q = "I passed MAT102H5 and MAT223H5. I want to take MAT301H5 as soon as possible. Plan my next three terms for "
    q += "the Mathematics minor starting 2027 Winter, max 2 courses per term."
    req = parse_request(q, "", store, TODAY)
    assert req is not None
    assert req.completed == ["MAT102H5", "MAT223H5"] and req.wanted == ["MAT301H5"]
    assert (req.start, req.terms, req.per_term) == ("2027 Winter", 3, 2)


def test_parse_request_uses_the_profile(store):
    context = "Courses completed: MAT102H5, MAT135H5. Courses in progress: MAT136H5. Program: ERMIN2511."
    req = parse_request("What should I take next term?", context, store, TODAY)
    assert req is not None and req.program == "ERMIN2511"
    assert req.completed == ["MAT102H5", "MAT135H5", "MAT136H5"]
    assert (req.start, req.terms) == ("2027 Winter", 1)
    assert parse_request("What should I take next term?", "", store, TODAY) is None


@pytest.mark.parametrize("q", QUESTIONS, ids=[q["id"] for q in QUESTIONS])
def test_every_eval_question_gets_a_valid_plan(store, q):
    plan = build_plan(q["question"], "", store, InMemoryCatalog(), TODAY)
    assert plan is not None and plan.terms
    check = validate_plan_text(plan.plan_text(), q["completed"], InMemoryCatalog())
    assert check["valid"], check["errors"]
    planned = [c for _, cs in plan.terms for c in cs]
    assert not set(planned) & set(q["completed"])
    if plan.request.terms is not None:
        assert len(plan.terms) <= plan.request.terms
    assert all(len(cs) <= plan.request.per_term for _, cs in plan.terms)
    # the eval's plan reader sees exactly the planned terms in the answer text (no note line reads as a term)
    assert extract_plan(plan_markdown(plan, store)) == plan.plan_text()


@pytest.mark.parametrize("q", QUESTIONS, ids=[q["id"] for q in QUESTIONS])
def test_planned_prerequisites_come_in_an_earlier_term(store, q):
    plan = build_plan(q["question"], "", store, InMemoryCatalog(), TODAY)
    assert plan is not None
    term_of = {c: i for i, (_, cs) in enumerate(plan.terms) for c in cs}
    for code, i in term_of.items():
        before = set(q["completed"]) | {c for c, j in term_of.items() if j < i}
        if evaluate(store.courses[code].get("prereq_tree"), before, True).status in ("met", "none"):
            continue
        # not plainly met (wording code can't check): every planned course it names must come earlier
        for needed in CODE.findall(store.courses[code].get("prerequisites") or ""):
            assert term_of.get(needed, -1) < i, f"{needed} is planned with or after {code}"


def test_wanted_course_goes_in_the_earliest_possible_term(store):
    q = next(q for q in QUESTIONS if q["id"] == "p4")  # "I want to take MAT301H5 as soon as possible"
    plan = build_plan(q["question"], "", store, InMemoryCatalog(), TODAY)
    assert plan is not None
    # MAT301H5 needs MAT202H5/MAT224H5/MAT240H5 first, so the term after that one is the earliest it can go
    assert plan.terms[0][0] == "2027 Winter" and "MAT301H5" in plan.terms[1][1]


def test_required_course_steers_first_year_choices(store):
    # The Specialist requires MAT257Y5 (needs MAT159H5); MAT139H5 and MAT134H5 exclude MAT159H5, so neither is picked.
    plan = build_plan(
        "Plan the first two terms of the Mathematical Sciences Specialist, starting 2026 Fall.",
        "",
        store,
        InMemoryCatalog(),
        TODAY,
    )
    assert plan is not None
    planned = {c for _, cs in plan.terms for c in cs}
    assert {"MAT157H5", "MAT159H5"} <= planned and not planned & {"MAT134H5", "MAT139H5"}
    assert "MAT257Y5" in plan.leftover


def test_unreadable_lines_and_missing_courses_are_reported_not_guessed(store):
    plan = build_plan(
        "Plan the Mathematics major starting 2027 Winter. Completed: MAT102H5, MAT135H5, MAT136H5.",
        "",
        store,
        InMemoryCatalog(),
        TODAY,
    )
    assert plan is not None
    assert "ISP100H5" in plan.not_in_data
    text = plan_markdown(plan, store)
    assert "ISP100H5" in text and "- **2027 Winter**:" in text
    assert "ISP100H5" not in plan.plan_text()


@pytest.mark.parametrize(
    "line,yes,no",
    [
        (
            "1.0 credit from the following: MAT at the 300/400 level or CSC363H5 (except for MAT397H5, MAT398H5)",
            ["MAT301H5", "MAT401H5", "CSC363H5"],
            ["MAT397H5", "MAT232H5"],
        ),
        ("2.0 additional credit from MAT302H5 or MAT309H5 or MAT311H5", ["MAT309H5"], ["MAT301H5"]),
        ("0.5 additional credits in MAT at the 300+level, except MAT322H5", ["MAT401H5"], ["MAT322H5", "MAT224H5"]),
        ("1.0 additional credits in MAT at the 400 level (MAT401H5 is recommended)", ["MAT402H5"], ["MAT301H5"]),
    ],
)
def test_elective_lines(line, yes, no):
    spec = _elective(line)
    assert spec is not None
    _, counts, _ = spec
    assert all(counts(c) for c in yes) and not any(counts(c) for c in no)


def test_elective_recommendation_is_read():
    spec = _elective("1.0 additional credits in MAT at the 400 level (MAT401H5 is recommended)")
    assert spec is not None and spec[2] == {"MAT401H5"}


async def test_runtime_streams_the_structured_plan_before_any_text():
    runtime = WaypointRuntime.from_settings(Settings(**OFFLINE, answer_cache="off"))
    q = QUESTIONS[0]["question"]
    kinds = []
    final = None
    async for kind, data in runtime.ask_stream("u1", q):
        kinds.append(kind)
        if kind == "plan":
            plan_event = data
        if kind == "final":
            final = data
    assert kinds.index("plan") < kinds.index("token")
    assert plan_event["valid"] and plan_event["terms"][0]["term"] == "2027 Winter"
    assert final is not None and final.record.route == "plan" and final.metrics()["model_calls"] == 0
    assert final.metrics()["plan_valid_final"] is True
    assert final.citations  # each planned course cites its calendar entry
    assert "academic advisor" in final.text


async def test_planning_without_a_program_asks_which_one(monkeypatch):
    runtime = WaypointRuntime.from_settings(Settings(**OFFLINE, answer_cache="off"))
    answer = await runtime.ask("u1", "Make a plan for me to finish a mathematics degree by 2029")
    assert answer.record.route == "plan" and "Which program should I plan toward?" in answer.text
    assert "ERMIN2511" in answer.text


async def test_fast_plan_off_uses_the_agent_planner():
    runtime = WaypointRuntime.from_settings(Settings(**OFFLINE, answer_cache="off", fast_plan="off"))
    answer = await runtime.ask("u1", QUESTIONS[0]["question"])
    assert answer.record.route != "plan" and "plan_drafter" in answer.record.agents
