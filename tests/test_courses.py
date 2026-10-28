import pytest

from waypoint.tools.catalog import NAMED_QUERIES, InMemoryCatalog
from waypoint.tools.courses import make_course_tools
from waypoint.tools.prereqs import check_prerequisites


@pytest.fixture(scope="module")
def tools():
    return {f.__name__: f for f in make_course_tools(InMemoryCatalog())}


def test_get_course_accepts_bare_code(tools):
    course = tools["get_course"]("mat224")
    assert course["found"] and course["code"] == "MAT224H5"
    assert tools["get_course"]("XYZ999")["found"] is False


def test_courses_unlocked_by(tools):
    codes = {c["code"] for c in tools["courses_unlocked_by"]("MAT224H5")["courses"]}
    assert {"MAT301H5", "MAT401H5"} <= codes


def test_get_program_by_name_and_code(tools):
    assert tools["get_program"]("ERMAJ2511")["programs"][0]["code"] == "ERMAJ2511"
    names = [p["code"] for p in tools["get_program"]("Applied Mathematics")["programs"]]
    assert names == ["ERMAJ2512"]


def test_search_courses(tools):
    result = tools["search_courses"]("combinatorics", "MAT")
    assert "MAT344H5" in {c["code"] for c in result["courses"]}


def test_check_course_prerequisites(tools):
    assert tools["check_course_prerequisites"]("MAT224", ["MAT102H5", "MAT223H5"])["status"] == "met"
    unmet = tools["check_course_prerequisites"]("MAT224", ["MAT102H5"])
    assert unmet["status"] == "unmet" and unmet["missing"] == ["MAT223H5"]


@pytest.mark.parametrize(
    ("text", "done", "status"),
    [
        ("MAT102H5 and (MAT223H5 or MAT240H5)", ["MAT102H5", "MAT240H5"], "met"),
        ("MAT102H5 and (MAT223H5 or MAT240H5)", ["MAT102", "MAT223"], "met"),
        ("MAT102H5 and (MAT223H5 or MAT240H5)", ["MAT223H5"], "unmet"),
        ("[MAT134H5 or MAT136H5] and MAT102H5", ["MAT136H5", "MAT102H5"], "met"),
        ("MAT102H5 (minimum 60%)", ["MAT102H5"], "unclear"),
        ("", [], "none"),
    ],
)
def test_prerequisite_evaluation(text, done, status):
    assert check_prerequisites(text, done).status == status


def test_only_allow_listed_queries():
    with pytest.raises(ValueError):
        InMemoryCatalog().run("DROP TABLE courses", {})
    assert all("@" in sql or name == "list_programs" for name, sql in NAMED_QUERIES.items())
