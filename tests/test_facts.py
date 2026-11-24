"""Fact route: question parsing, structured answers, prerequisite evaluation, and the runtime wiring."""

import asyncio
from typing import Any

import pytest

from waypoint.agents.runtime import WaypointRuntime
from waypoint.config import Settings
from waypoint.datastore import get_store
from waypoint.facts import answer_fact, parse_fact_question, rephrase_ok
from waypoint.ingest.prereq_parser import parse_prereq
from waypoint.prereq_eval import evaluate

STORE = get_store()


def ask(text: str, have: set[str] | None = None, hs: bool = False) -> Any:
    query = parse_fact_question(text, STORE)
    assert query is not None, text
    return query, answer_fact(query, STORE, have, hs)


@pytest.mark.parametrize(
    ("question", "kind"),
    [
        ("What do I need before MAT224H5?", "prerequisites"),
        ("what are the prerequisites for MAT224", "prerequisites"),
        ("Which courses list MAT223H5 as a prerequisite?", "unlocks"),
        ("What does MAT102H5 unlock?", "unlocks"),
        ("Is MATB24H3 listed as an exclusion of MAT224H5?", "is_exclusion"),
        ("What are the exclusions of MAT224H5?", "exclusions"),
        ("Is there an equivalent of MAT224H5 at UTSC?", "equivalents"),
        ("How many credits is MAT224H5?", "credits"),
        ("Can I take MAT224H5?", "can_take"),
        ("Tell me about MAT224H5", "overview"),
        ("What are the requirements for the Mathematical Sciences minor?", "program"),
    ],
)
def test_question_kinds(question: str, kind: str) -> None:
    query = parse_fact_question(question, STORE)
    assert query is not None and query.kind == kind


@pytest.mark.parametrize("question", ["Is MAT224H5 hard?", "How do I petition a grade?", "Make me a plan to graduate"])
def test_non_fact_questions_fall_through(question: str) -> None:
    assert parse_fact_question(question, STORE) is None


def test_prerequisites_answer_is_exact_and_cited() -> None:
    _, answer = ask("What do I need before MAT224H5?")
    assert "MAT102H5 and MAT223H5" in answer.text
    assert answer.chunk_ids == ["cal:mathematical-sciences#mat224h5"]
    assert f"[{answer.chunk_ids[0]}]" in answer.text


def test_and_or_structure_is_kept() -> None:
    _, answer = ask("prerequisites of MAT236H5")
    assert "(MAT223H5 or MAT240H5) and (MAT232H5 or MAT233H5)" in answer.text


def test_unlocks_uses_any_depth_of_the_tree() -> None:
    _, answer = ask("Which courses list MAT223H5 as a prerequisite?")
    for code in ("MAT224H5", "MAT236H5", "MAT344H5"):
        assert code in answer.text


def test_cross_campus_exclusion_question() -> None:
    _, yes = ask("Is MATB24H3 listed as an exclusion of MAT224H5?")
    assert yes.text.startswith("Yes: MATB24H3 is listed as an exclusion of MAT224H5")
    _, no = ask("Is MATC44H3 an exclusion of MAT224H5?")
    assert no.text.startswith("No:")


def test_equivalents_do_not_promise_eligibility() -> None:
    _, answer = ask("equivalent of MAT224H5 at UTSC")
    assert "MATB24H3 (UTSC)" in answer.text and "MAT224H1 (St. George)" in answer.text
    assert "can't promise eligibility" in answer.text


def test_unknown_course_says_check_the_calendar_and_invents_nothing() -> None:
    _, answer = ask("What are the prerequisites of CSC108H5?")
    assert (
        "isn't in Waypoint's data yet" in answer.text
        and "https://utm.calendar.utoronto.ca/course/CSC108H5" in answer.text
    )
    assert "need" not in answer.text.lower().replace("isn't", "")


def test_can_take_uses_the_profile() -> None:
    _, missing = ask("Can I take MAT224H5?", {"MAT102H5"})
    assert "still missing MAT223H5" in missing.text
    _, ok = ask("Can I take MAT224H5?", {"MAT102H5", "MAT223H5"})
    assert ok.text.startswith("Yes, the courses in your profile meet")
    _, none = ask("Can I take MAT224H5?", set())
    assert "Add the courses you've taken" in none.text


def test_program_answer_separates_admission_from_prerequisites() -> None:
    _, answer = ask("What are the requirements for the Mathematical Sciences specialist?")
    assert "14 credits are required" in answer.text
    assert "To get into the program (separate from course prerequisites)" in answer.text


def test_evaluate_three_valued() -> None:
    tree = parse_prereq("(65% in MAT102H5) or MAT157H5")
    assert evaluate(tree, {"MAT157H5"}).status == "met"
    assert evaluate(tree, set()).status == "unmet"
    graded = evaluate(tree, {"MAT102H5"})
    assert graded.status == "unclear" and "65%" in graded.unchecked[0]
    hs = parse_prereq("Minimum 70% in Grade 12 Advanced Functions (MHF4U)")
    assert evaluate(hs, set()).status == "unclear"
    assert evaluate(hs, set(), highschool_done=True).status == "met"
    assert evaluate(None, set()).status == "none"


def test_rephrase_guard_rejects_additions() -> None:
    facts = "MAT224H5 needs MAT102H5 [cal:x#y]."
    assert rephrase_ok(facts, "You need MAT102H5 first for MAT224H5 [cal:x#y].")
    assert not rephrase_ok(facts, "You need MAT102H5 and MAT999H5 [cal:x#y].")
    assert not rephrase_ok(facts, "You need MAT102H5 [cal:other#z].")
    assert not rephrase_ok(facts, "  ")


async def _run_question(text: str, context: str = "") -> tuple[Any, list[tuple[str, Any]]]:
    runtime = WaypointRuntime.from_settings(Settings(llm="mock", store="memory", catalog="memory", sessions="memory"))
    events: list[tuple[str, Any]] = []
    answer = None
    async for kind, data in runtime.ask_stream("u1", text, context=context):
        events.append((kind, data))
        if kind == "final":
            answer = data
    return answer, events


def test_runtime_serves_facts_without_any_model_call() -> None:
    answer, events = asyncio.run(_run_question("What do I need before MAT224H5?"))
    assert answer.record.route == "fact" and answer.record.category == "fact"
    assert answer.metrics()["model_calls"] == 0
    assert "MAT102H5 and MAT223H5" in answer.text
    kinds = [k for k, _ in events]
    assert kinds.index("sources") < kinds.index("token")
    assert answer.citations == ["cal:mathematical-sciences#mat224h5"]


def test_runtime_fact_route_can_be_switched_off() -> None:
    runtime = WaypointRuntime.from_settings(
        Settings(llm="mock", store="memory", catalog="memory", sessions="memory", fact_route="off")
    )
    answer = asyncio.run(runtime.ask("u1", "What do I need before MAT224H5?"))
    assert answer.record.route != "fact"


def test_planning_over_budget_ends_with_a_pointer_to_my_plan_not_a_step_limit() -> None:
    settings = Settings(llm="mock", embeddings="mock", store="memory", catalog="memory", sessions="memory")
    runtime = WaypointRuntime.from_settings(settings.model_copy(update={"plan_budget_s": 0.001, "fast_plan": "off"}))
    answer = asyncio.run(runtime.ask("u1", "Make a plan for me to finish a mathematics degree by 2029"))
    assert answer.record.category == "planning"
    assert "Open **My plan**" in answer.text
    assert "step limit" not in answer.text.lower()
