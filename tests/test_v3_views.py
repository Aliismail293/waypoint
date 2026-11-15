"""V3 backends for the course map, Programs tab and Enrolment check, plus their API routes."""

from datetime import date

from fastapi.testclient import TestClient

from waypoint.agents.runtime import WaypointRuntime
from waypoint.api.app import create_app
from waypoint.config import Settings
from waypoint.coursemap import ancestors, build_map
from waypoint.datastore import get_store
from waypoint.enrolment import acorn_list, current_term, enrolment_check, pick_term
from waypoint.plans import Plan, PlanTerm
from waypoint.profile import Profile, ProfileCourse
from waypoint.programs import checklist

STORE = get_store()


def prof(done=(), now=(), **kw) -> Profile:
    courses = [ProfileCourse(code=c, status="done") for c in done] + [ProfileCourse(code=c, status="now") for c in now]
    return Profile(courses=courses, **kw)


def by_code(result):
    return {n["code"]: n for n in result["nodes"]}


def test_map_places_courses_by_band_and_depth_and_draws_real_edges_only() -> None:
    m = by_code(build_map(STORE, prof(), set(), "utm", {"MAT"}))
    assert m["MAT135H5"]["band"] == 1 and m["MAT135H5"]["sub"] == 0
    assert m["MAT136H5"]["band"] == 1 and m["MAT136H5"]["sub"] == 1  # needs MAT135H5 (one of several), same band
    assert m["MAT299H5"]["band"] == 2 and m["MAT299H5"]["sub"] == 0  # lists no prerequisite: first sub-column
    assert m["MAT224H5"]["sub"] == 1  # MAT102H5 is in another band, MAT223H5 in the same band
    edges = build_map(STORE, prof(), set(), "utm", {"MAT"})["edges"]
    assert not [e for e in edges if e["to"] == "MAT299H5"]  # and no incoming line
    assert {"from": "MAT135H5", "to": "MAT136H5", "kind": "one_of", "group": 1} in edges
    assert {"from": "MAT223H5", "to": "MAT224H5", "kind": "all", "group": 0} in edges


def test_or_groups_use_their_shallowest_member() -> None:
    m = by_code(build_map(STORE, prof(), set(), "utm", {"MAT"}))
    # MAT134H5 needs ONE of five courses; MAT132H5 is depth 0, so MAT134H5 is depth 1, not deeper via MAT136H5
    assert m["MAT134H5"]["sub"] == 1


def test_prerequisites_outside_the_filter_arrive_as_ghosts_never_dropped() -> None:
    result = build_map(STORE, prof(), set(), "utm", {"MAT"})
    ghosts = [n for n in result["nodes"] if n["ghost"]]
    assert {g["code"] for g in ghosts} >= {"MAT157Y1", "MATA29H3"}  # other campuses' courses named as prerequisites
    assert all(g["subject"] for g in ghosts)
    assert {e["from"] for e in result["edges"]} >= {g["code"] for g in ghosts}


def test_status_colours_come_from_profile_and_plan() -> None:
    m = by_code(build_map(STORE, prof(done=["MAT102H5"], now=["MAT223H5"]), {"MAT224H5"}, "utm", {"MAT"}))
    assert (m["MAT102H5"]["status"], m["MAT223H5"]["status"], m["MAT224H5"]["status"]) == ("done", "now", "planned")


def test_path_is_the_transitive_prerequisite_closure() -> None:
    path = ancestors(STORE.edges(), "MAT224H5")
    assert {"MAT224H5", "MAT102H5", "MAT223H5"} <= set(path["nodes"])
    assert all(e["to"] in path["nodes"] for e in path["edges"])


def test_checklist_statuses_and_separate_admission() -> None:
    profile = prof(done=["MAT102H5"], now=["MAT223H5"], programs=["ERMIN2511"], program="ERMIN2511")
    result = checklist(STORE, "ERMIN2511", profile, {"MAT232H5"})
    rows = {r["text"]: r for g in result["versions"][0]["groups"] for r in g["requirements"]}
    assert rows["MAT102H5"]["status"] == "done"
    assert rows["MAT223H5 or MAT240H5"]["status"] == "in_progress"
    assert any(r["status"] == "check" for r in rows.values())  # "1.0 credit from ... 300/400 level" stays unparsed
    assert result["credits"] == {"done": 0.5, "required": [4.0, 4.0]}
    assert "admission" in result and result["calendar_url"].startswith("https://utm.calendar")
    assert checklist(STORE, "NOPE", profile, set()) is None


def test_missing_requirements_list_what_is_missing() -> None:
    result = checklist(STORE, "ERMIN2511", prof(), set())
    first = result["versions"][0]["groups"][0]["requirements"][0]
    assert first["status"] == "missing" and first["missing"] == ["MAT102H5"]


def plan_with(term, courses) -> Plan:
    return Plan(terms=[PlanTerm(term=term, courses=courses)])


def test_enrolment_check_collects_notes_conflicts_and_other_campuses() -> None:
    plans = [plan_with("2027 Winter", ["MAT224H5", "MAT240H5", "MAT202H5"])]
    result = enrolment_check(STORE, prof(done=["MAT102H5"]), plans, "2027 Winter")
    rows = {r["code"]: r for r in result["courses"]}
    assert any("MAT240H5" in c for c in rows["MAT224H5"]["conflicts"])  # excludes a planned course
    assert {o["code"] for o in rows["MAT224H5"]["other_campuses"]} >= {"MATB24H3", "MAT224H1"}
    assert rows["MAT202H5"]["notes"] and "Priority" in rows["MAT202H5"]["notes"][0]
    assert rows["MAT224H5"]["status"] == "current" and "Not checked" in rows["MAT224H5"]["offered"]
    assert "cross-campus" in result["cross_campus_note"] and "promise" in result["cross_campus_note"]
    assert result["acorn"].startswith("Courses to search for on ACORN, 2027 Winter:")


def test_enrolment_check_with_no_plan_and_term_picking() -> None:
    assert enrolment_check(STORE, prof(), [], None)["courses"] == []
    plans = [
        Plan(
            terms=[PlanTerm(term="2026 Fall", courses=["MAT102H5"]), PlanTerm(term="2027 Winter", courses=["MAT223H5"])]
        )
    ]
    assert pick_term(plans, None, date(2026, 9, 1)) == "2026 Fall"
    assert pick_term(plans, None, date(2026, 11, 20)) == "2027 Winter"
    assert pick_term(plans, "2026 Fall") == "2026 Fall"
    assert current_term(date(2026, 4, 1)) == "2026 Summer" and current_term(date(2026, 12, 1)) == "2027 Winter"
    assert "- MAT224H5" in acorn_list("2027 Winter", ["MAT224H5"])


def test_api_routes_and_no_sample_timetable_in_user_path() -> None:
    settings = Settings(
        llm="mock", embeddings="mock", store="memory", catalog="memory", sessions="memory", dev_skip_auth=True
    )
    with TestClient(create_app(settings, WaypointRuntime.from_settings(settings))) as client:
        check_routes(client)


def check_routes(client: TestClient) -> None:
    assert client.get("/api/v3/map", params={"campus": "utm", "subject": "MAT"}).json()["nodes"]
    assert client.get("/api/v3/map/path/MAT224H5").json()["nodes"]
    programs = client.get("/api/v3/programs", params={"q": "minor"}).json()["programs"]
    assert [p["code"] for p in programs] == ["ERMIN2511"]
    assert client.get("/api/v3/programs/ERMIN2511").json()["credits"]["required"] == [4.0, 4.0]
    assert client.get("/api/v3/programs/NOPE").status_code == 404
    assert client.get("/api/v3/enrolment").json()["courses"] == []
