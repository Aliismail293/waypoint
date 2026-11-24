"""Prerequisite parser: golden strings with hand-written expected trees, plus coverage on the real UTM data."""

import json
from pathlib import Path
from typing import Any

import pytest

from waypoint.ingest.prereq_parser import course_leaves, describe, is_fully_parsed, parse_prereq

GOLDEN = json.loads(Path(__file__).parent.joinpath("data/prereq_golden.json").read_text())["cases"]


def leaf(spec: str) -> dict[str, Any]:
    """'MAT135H5@65' / 'hs:MHF4U@70' / 'cr:0.5:MAT:300:add:60' / 'perm:x' / 'cgpa:3.2' / 'unparsed' -> node."""
    if spec == "unparsed":
        return {"unparsed": "*"}
    if spec.startswith("hs:"):
        code, _, grade = spec[3:].partition("@")
        return {"highschool": code} | ({"min_grade": int(grade)} if grade else {})
    if spec.startswith("cr:"):
        parts = spec.split(":")[1:]
        node: dict[str, Any] = {"credits": float(parts[0]), "subject": parts[1]}
        if len(parts) > 2:
            node["level"] = int(parts[2])
        if len(parts) > 3:
            node["additional"] = True
        if len(parts) > 4:
            node["min_grade"] = int(parts[4])
        return node
    if spec.startswith("perm:"):
        return {"permission": spec[5:]}
    if spec.startswith("cgpa:"):
        return {"cgpa": float(spec[5:])}
    code, _, grade = spec.partition("@")
    return {"course": code} | ({"min_grade": int(grade)} if grade else {})


def expected(spec: Any) -> Any:
    if spec is None:
        return None
    if isinstance(spec, list):
        return {{"all": "all_of", "any": "one_of"}[spec[0]]: [expected(s) for s in spec[1:]]}
    return leaf(spec)


def blank_unparsed(tree: Any) -> Any:
    """Compare structure only for unparsed leaves: the exact text is checked separately."""
    if isinstance(tree, dict):
        if "unparsed" in tree:
            return {"unparsed": "*"}
        return {k: blank_unparsed(v) for k, v in tree.items()}
    if isinstance(tree, list):
        return [blank_unparsed(v) for v in tree]
    return tree


def test_golden_set_is_big_enough_and_labelled() -> None:
    assert len(GOLDEN) >= 60
    assert {c["source"] for c in GOLDEN} == {"utm-calendar", "format"}


@pytest.mark.parametrize("case", GOLDEN, ids=[f"{i}-{c['text'][:40]}" for i, c in enumerate(GOLDEN)])
def test_golden(case: dict[str, Any]) -> None:
    assert blank_unparsed(parse_prereq(case["text"])) == expected(case["expect"])


def test_unparsed_keeps_the_exact_text() -> None:
    assert parse_prereq("Admission to the Statistics Specialist program") == {
        "unparsed": "Admission to the Statistics Specialist program"
    }


def test_leaves_and_description() -> None:
    tree = parse_prereq("MAT102H5 and (MAT223H5 or MAT240H5)")
    assert course_leaves(tree) == ["MAT102H5", "MAT223H5", "MAT240H5"]
    assert describe(tree) == "MAT102H5 and (MAT223H5 or MAT240H5)"
    assert describe(None) == "No prerequisites listed."
    assert "check the calendar" in describe(parse_prereq("something odd"))


def test_real_utm_parse_coverage() -> None:
    rows = [json.loads(line) for line in Path("data/corpus/courses.jsonl").read_text().splitlines()]
    parsed = sum(is_fully_parsed(parse_prereq(r["prerequisites"])) for r in rows)
    assert parsed / len(rows) >= 0.85
