"""Student profile, course map graph and the personalised API, all offline."""

import pytest
from fastapi.testclient import TestClient

from waypoint.agents.runtime import WaypointRuntime
from waypoint.api import auth
from waypoint.api.app import create_app
from waypoint.config import Settings
from waypoint.graph import build_graph, find_route
from waypoint.profile import (
    MemoryProfileStore,
    Profile,
    ProfileCourse,
    ProfileError,
    looks_like_student_number,
    profile_context,
    validate_profile,
)
from waypoint.tools.catalog import InMemoryCatalog

# fact_route off: these tests exercise the agent/router/stream paths; the fact route has tests/test_facts.py
OFFLINE = dict(llm="mock", embeddings="mock", store="memory", catalog="memory", sessions="memory", fact_route="off")


class FixtureCatalog:
    """A tiny hand-made catalog: A100 -> B200 -> C300 -> D400, E200 -> D400, and F300 with no links at all."""

    COURSES = {
        "AAA100H5": ("Intro", 1),
        "BBB200H5": ("Second", 2),
        "CCC300H5": ("Third", 3),
        "DDD400H5": ("Fourth", 4),
        "EEE200H5": ("Side", 2),
        "FFF300H5": ("Alone", 3),
    }
    EDGES = [("BBB200H5", "AAA100H5"), ("CCC300H5", "BBB200H5"), ("DDD400H5", "CCC300H5"), ("DDD400H5", "EEE200H5")]

    def run(self, name, params):
        if name == "list_courses":
            return [{"code": c, "title": t, "level": lv, "credits": 0.5} for c, (t, lv) in self.COURSES.items()]
        if name == "prerequisite_edges":
            return [{"course_code": c, "required_code": r} for c, r in self.EDGES]
        if name == "get_course":
            return [{"code": c} for c in self.COURSES if c.startswith(params["code"])]
        if name == "get_program":
            return [{"code": "PROG1"}] if params["code"] == "PROG1" else []
        raise AssertionError(name)


def profile(done=(), now=()):
    return Profile(
        courses=[ProfileCourse(code=c, status="done") for c in done]
        + [ProfileCourse(code=c, status="now") for c in now]
    )


# ---- validation ----


def test_valid_profile_is_normalised_and_deduplicated():
    raw = {
        "courses": [{"code": "aaa100h5", "status": "done"}, {"code": "BBB200H5", "status": "now"}],
        "program": "prog1",
        "grad_year": 2028,
    }
    p = validate_profile(raw, FixtureCatalog(), today=__import__("datetime").date(2026, 11, 14))
    assert p.codes("done") == ["AAA100H5"] and p.codes("now") == ["BBB200H5"]
    assert p.program == "PROG1" and p.grad_year == 2028


@pytest.mark.parametrize(
    "raw",
    [
        {"courses": [{"code": "ZZZ999H5", "status": "done"}]},  # not in the catalog
        {"courses": [{"code": "not a course", "status": "done"}]},
        {"courses": [{"code": "1234567890", "status": "done"}]},  # a student number pasted as a course
        {"courses": [{"code": "AAA100H5", "status": "done", "grade": 91}, {"code": "AAA100H5", "status": "maybe"}]},
        {"program": "student 1002345678"},
        {"program": "NOPE"},
        {"grad_year": 1999},
        {"grad_year": "2028"},
    ],
)
def test_bad_profiles_are_rejected(raw):
    with pytest.raises(ProfileError):
        validate_profile(raw, FixtureCatalog())


def test_grades_are_never_kept():
    p = validate_profile({"courses": [{"code": "AAA100H5", "status": "done", "grade": 91}]}, FixtureCatalog())
    assert "grade" not in p.model_dump_json() and "91" not in p.model_dump_json()


def test_student_number_detector():
    assert looks_like_student_number("1002345678") and looks_like_student_number("1002 3456 78")
    assert not looks_like_student_number("ERMAJ2511") and not looks_like_student_number("2028")


def test_profile_context_has_only_codes_status_program_year():
    text = profile_context(
        profile(done=["AAA100H5"], now=["BBB200H5"]).model_copy(update={"program": "P", "grad_year": 2028})
    )
    assert text == (
        "Courses completed: AAA100H5. Courses in progress: BBB200H5. Program: P. Expected graduation year: 2028."
    )
    assert profile_context(Profile()) == ""


def test_store_round_trip_and_delete_is_per_user():
    store = MemoryProfileStore()
    store.put("alice", profile(done=["AAA100H5"]))
    assert store.get("bob") is None
    assert store.get("alice").codes("done") == ["AAA100H5"]
    store.delete("alice")
    assert store.get("alice") is None


# ---- graph and routes ----


def test_graph_uses_only_catalog_edges_and_colours_by_status():
    g = build_graph(FixtureCatalog(), profile(done=["AAA100H5"], now=["BBB200H5"]))
    assert {(e["from"], e["to"]) for e in g.edges} == {(r, c) for c, r in FixtureCatalog.EDGES}
    assert g.nodes["AAA100H5"]["column"] == "before" and g.nodes["BBB200H5"]["column"] == "now"
    assert g.nodes["CCC300H5"]["column"] == "upper" and g.nodes["EEE200H5"]["column"] == "lower"
    assert g.nodes["FFF300H5"]["status"] is None


def test_edges_to_courses_outside_the_data_are_kept_but_flagged():
    class WithOutside(FixtureCatalog):
        def run(self, name, params):
            rows = super().run(name, params)
            if name == "prerequisite_edges":
                rows = [*rows, {"course_code": "BBB200H5", "required_code": "XYZ101H1"}]
            return rows

    g = build_graph(WithOutside(), Profile())
    assert g.nodes["XYZ101H1"]["in_data"] is False and g.nodes["AAA100H5"]["in_data"] is True


def test_route_follows_links_from_the_students_courses():
    p = profile(done=["AAA100H5"])
    g = build_graph(FixtureCatalog(), p)
    route = find_route(g, "ddd400h5", p)
    assert route["state"] == "route"
    assert [s["code"] for s in route["steps"]] == ["AAA100H5", "BBB200H5", "CCC300H5", "DDD400H5"]
    assert route["missing"] == ["CCC300H5", "EEE200H5"]  # direct prerequisites still to take


def test_route_picks_the_shortest_chain():
    p = profile(done=["AAA100H5"], now=["EEE200H5"])
    route = find_route(build_graph(FixtureCatalog(), p), "DDD400H5", p)
    assert [s["code"] for s in route["steps"]] == ["EEE200H5", "DDD400H5"]


def test_no_route_found_is_reported_not_invented():
    p = profile(done=["FFF300H5"])  # a course with no links to the target
    route = find_route(build_graph(FixtureCatalog(), p), "DDD400H5", p)
    assert route["state"] == "no_route" and route["steps"] == []
    assert find_route(build_graph(FixtureCatalog(), p), "FFF300H5", p)["state"] == "already_taken"
    assert find_route(build_graph(FixtureCatalog(), Profile()), "AAA100H5", Profile())["state"] == "no_links"
    assert find_route(build_graph(FixtureCatalog(), p), "NOPE101H5", p)["state"] == "unknown_course"


def test_real_catalog_graph_has_the_documented_edges():
    catalog = InMemoryCatalog()
    g = build_graph(catalog, profile(done=["MAT102H5"], now=["MAT223H5"]))
    assert ("MAT223H5", "MAT224H5") in {(e["from"], e["to"]) for e in g.edges}
    assert find_route(g, "MAT224H5", profile(done=["MAT102H5"], now=["MAT223H5"]))["state"] == "route"


# ---- API: profile, personalisation, isolation ----


@pytest.fixture
def client(monkeypatch):
    def claims(token, req, audience=None):
        return {
            "iss": "https://accounts.google.com",
            "aud": "cid",
            "email": f"{token}@x.ca",
            "email_verified": True,
            "sub": token,
        }

    monkeypatch.setattr(auth.id_token, "verify_oauth2_token", claims)
    settings = Settings(**OFFLINE, google_client_id="cid")
    with TestClient(create_app(settings, WaypointRuntime.from_settings(settings))) as c:
        yield c


def as_user(name):
    return {"Authorization": f"Bearer {name}"}


def save(client, user, courses, **extra):
    body = {"courses": [{"code": c, "status": s} for c, s in courses], **extra}
    return client.put("/api/profile", json=body, headers=as_user(user))


def test_profile_api_round_trip_validation_and_delete(client):
    assert client.get("/api/profile", headers=as_user("ana")).json()["onboarded"] is False
    ok = save(client, "ana", [("MAT102H5", "done"), ("MAT223H5", "now")], grad_year=2028)
    assert ok.status_code == 200, ok.text
    assert client.get("/api/profile", headers=as_user("ana")).json()["courses"][0]["code"] == "MAT102H5"
    assert save(client, "ana", [("ZZZ999H5", "done")]).status_code == 422
    assert client.put("/api/profile", json={"program": "1002345678"}, headers=as_user("ana")).status_code == 422
    assert client.delete("/api/profile", headers=as_user("ana")).json() == {"ok": True}
    assert client.get("/api/profile", headers=as_user("ana")).json()["courses"] == []


def test_profile_endpoints_need_sign_in(client):
    for method, path in [
        ("get", "/api/profile"),
        ("put", "/api/profile"),
        ("delete", "/api/profile"),
        ("get", "/api/map"),
    ]:
        assert client.request(method, path, json={} if method == "put" else None).status_code == 401


def test_autocomplete_map_route_and_course_page(client):
    h = as_user("ana")
    assert {c["code"] for c in client.get("/api/courses?q=mat22", headers=h).json()["courses"]} >= {
        "MAT223H5",
        "MAT224H5",
    }
    assert client.get("/api/courses?q=", headers=h).json()["courses"] == []
    save(client, "ana", [("MAT102H5", "done"), ("MAT223H5", "now")])
    graph = client.get("/api/map", headers=h).json()
    assert {"from": "MAT223H5", "to": "MAT224H5"} in graph["edges"]
    statuses = {n["code"]: n["status"] for n in graph["nodes"]}
    assert statuses["MAT102H5"] == "done" and statuses["MAT223H5"] == "now"
    assert client.get("/api/route/MAT224H5", headers=h).json()["state"] == "route"

    page = client.get("/api/course/MAT224H5", headers=h).json()
    assert page["calendar"]["source_url"].startswith("https://utm.calendar.utoronto.ca/")
    assert "sections" not in page and "sections_source" not in page  # V3: no sample timetable on the course page
    assert {r["code"]: r["status"] for r in page["prerequisite_check"]["required"]}["MAT223H5"] == "now"
    assert client.get("/api/course/NOPE101H5", headers=h).status_code == 404


def test_course_page_leaves_missing_calendar_fields_empty_instead_of_inventing(client):
    page = client.get("/api/course/MAT102H5", headers=as_user("ana")).json()
    assert page["calendar"]["corequisites"] is None  # "" in the data -> explicit empty state in the UI


def ask(client, user, question):
    return client.post("/api/chat", json={"message": question}, headers=as_user(user)).json()["answer"]


def test_profile_changes_the_answer_and_never_leaks_into_another_session(client):
    question = "Can I take MAT224H5?"
    save(client, "ana", [("MAT102H5", "done"), ("MAT223H5", "done")])
    save(client, "ben", [])
    with_profile = ask(client, "ana", question)
    without = ask(client, "ben", question)
    assert '"status": "met"' in with_profile  # the checker was given ana's completed courses
    assert '"status": "unmet"' in without  # ben has no profile: nothing is assumed
    # ben asks again after ana's answer is cached: he must not get ana's personalised answer
    assert '"status": "unmet"' in ask(client, "ben", question)
    # and nothing of ana's profile text appears anywhere in ben's answer
    assert "Courses completed" not in without
