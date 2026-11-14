"""Plans, the deterministic critic report and the timetable builder, all offline."""

import pytest
from fastapi.testclient import TestClient

from waypoint.agents.runtime import WaypointRuntime
from waypoint.api import auth
from waypoint.api.app import create_app
from waypoint.config import Settings
from waypoint.mcp.timetable.adapter import SampleFileSource, TimetableAdapter
from waypoint.plans import (
    MemoryPlanStore,
    Plan,
    PlanError,
    PlanTerm,
    add_course,
    next_term,
    plan_text,
    review_plan,
    suggest_term,
    tidy,
)
from waypoint.profile import Profile, ProfileCourse
from waypoint.timetable_check import find_clashes, minutes, suggest_fixes, timetable_view
from waypoint.tools.catalog import InMemoryCatalog
from waypoint.tools.plan import validate_plan_text

CATALOG = InMemoryCatalog()
OFFLINE = dict(llm="mock", embeddings="mock", store="memory", catalog="memory", sessions="memory")


def plan(*terms: tuple[str, list[str]]) -> Plan:
    return Plan(terms=[PlanTerm(term=t, courses=c) for t, c in terms])


def profile(done=(), now=(), program="ERMAJ2511") -> Profile:
    courses = [ProfileCourse(code=c, status="done") for c in done] + [ProfileCourse(code=c, status="now") for c in now]
    return Profile(courses=courses, program=program)


def levels(report, level):
    return [i for i in report["items"] if i["level"] == level]


# ---- plan structure ----


def test_tidy_orders_terms_chronologically_and_drops_repeats():
    p = tidy(plan(("2027 Winter", ["mat224h5"]), ("2026 Fall", ["MAT223H5", "MAT224H5"]), ("2026 Summer", [])))
    assert [t.term for t in p.terms] == ["2026 Summer", "2026 Fall", "2027 Winter"]
    assert p.terms[1].courses == ["MAT223H5", "MAT224H5"] and p.terms[2].courses == []  # first mention wins


@pytest.mark.parametrize("bad", [plan(("Fall 2026", ["MAT223H5"])), plan(("2026 Fall", ["hello"]))])
def test_tidy_rejects_bad_terms_and_codes(bad):
    with pytest.raises(PlanError):
        tidy(bad)


def test_next_term():
    assert next_term("2026 Fall") == "2027 Winter" and next_term("2027 Winter") == "2027 Fall"


def test_plan_text_is_the_form_the_validator_reads():
    assert plan_text(plan(("2026 Fall", ["A", "B"]), ("2027 Winter", []), ("2027 Fall", ["C"]))) == (
        "2026 Fall: A, B; 2027 Fall: C"
    )


def test_add_course_goes_to_the_earliest_term_where_prerequisites_are_met():
    p = plan(("2026 Fall", ["MAT223H5"]), ("2027 Winter", []))
    updated = add_course(p, "mat224h5", None, profile(done=["MAT102H5"]), CATALOG)
    assert next(t.term for t in updated.terms if "MAT224H5" in t.courses) == "2027 Winter"  # after MAT223H5
    assert "MAT224H5" not in updated.terms[0].courses


def test_add_course_opens_a_new_term_when_none_fits_and_refuses_duplicates():
    full = plan(("2026 Fall", ["MAT102H5", "MAT223H5", "MAT232H5", "MAT202H5", "MAT236H5"]))  # 2.5 credits
    assert suggest_term(full, "MAT244H5", profile(done=["MAT102H5", "MAT223H5"]), CATALOG) == "2027 Winter"
    with pytest.raises(PlanError):
        add_course(full, "MAT102H5", None, profile(), CATALOG)
    with pytest.raises(PlanError):
        add_course(full, "ZZZ999H5", None, profile(), CATALOG)


# ---- the critic report ----


def test_prerequisite_order_error_has_a_citation_and_goes_away_when_fixed():
    broken = review_plan(plan(("2026 Fall", ["MAT224H5"])), profile(), CATALOG)
    errors = levels(broken, "error")
    assert broken["status"] == "errors" and "MAT224H5" in errors[0]["text"] and "MAT223H5" in errors[0]["text"]
    assert errors[0]["chunk_ids"] == ["cal:mathematical-sciences#mat224h5"]

    fixed = review_plan(
        plan(("2026 Fall", ["MAT102H5", "MAT223H5"]), ("2027 Winter", ["MAT224H5"])), profile(), CATALOG
    )
    assert not levels(fixed, "error")
    assert levels(fixed, "ok")[0]["text"].startswith("Every planned course comes after")
    assert "cal:mathematical-sciences#mat224h5" in levels(fixed, "ok")[0]["chunk_ids"]


def test_same_term_prerequisite_is_not_enough():
    report = review_plan(plan(("2026 Fall", ["MAT102H5", "MAT223H5", "MAT224H5"])), profile(), CATALOG)
    assert any("MAT224H5" in i["text"] and "needs" in i["text"] for i in levels(report, "error"))


def test_courses_the_student_has_or_is_taking_count_as_in_hand():
    p = plan(("2027 Winter", ["MAT224H5"]))
    assert not levels(review_plan(p, profile(done=["MAT102H5"], now=["MAT223H5"]), CATALOG), "error")


def test_credit_load_per_term_and_exclusions_and_repeats():
    six = ["MAT102H5", "MAT223H5", "MAT232H5", "MAT202H5", "MAT236H5", "MAT244H5"]  # 3.0 credits
    heavy = review_plan(plan(("2026 Fall", six)), profile(), CATALOG)
    assert any("3 credits" in i["text"] for i in levels(heavy, "error"))
    exclusion = review_plan(plan(("2026 Fall", ["MAT102H5"])), profile(done=["CSC110Y5"]), CATALOG)
    assert any("exclusion" in i["text"] for i in levels(exclusion, "error"))
    again = review_plan(plan(("2026 Fall", ["MAT102H5"])), profile(done=["MAT102H5"]), CATALOG)
    assert any("already taken" in i["text"] for i in levels(again, "warn"))


def test_program_credit_progress_cites_the_program_and_is_honest_when_unknown():
    p = plan(("2026 Fall", ["MAT102H5", "MAT223H5"]))
    low = review_plan(p, profile(program="ERMAJ2511"), CATALOG)
    note = next(i for i in levels(low, "warn") if "needs" in i["text"] and "credits" in i["text"])
    assert "8.5–9" in note["text"] and note["chunk_ids"]  # cited from the program record
    assert low["credits"]["required"] == [8.5, 9.0] and low["credits"]["counted"] == 1.0
    nothing = review_plan(p, profile(program=""), CATALOG)
    assert nothing["credits"]["required"] is None and "Set your program" in levels(nothing, "warn")[-1]["text"]


def test_empty_plan_and_unknown_course_do_not_crash():
    assert review_plan(Plan(), profile(), CATALOG)["status"] == "empty"
    unknown = review_plan(plan(("2026 Fall", ["ZZZ999H5"])), profile(), CATALOG)
    assert "isn't in Waypoint's course data" in levels(unknown, "error")[0]["text"]


@pytest.mark.parametrize(
    "terms",
    [
        [("2026 Fall", ["MAT224H5"])],
        [("2026 Fall", ["MAT102H5", "MAT223H5"]), ("2027 Winter", ["MAT224H5"])],
        [("2026 Fall", ["MAT102H5"]), ("2027 Winter", ["MAT102H5"])],  # tidy removes the repeat; same either way
        [("2026 Fall", ["MAT102H5", "MAT223H5", "MAT232H5", "MAT202H5", "MAT236H5", "MAT244H5"])],
    ],
)
def test_review_agrees_with_the_existing_plan_validator(terms):
    p = tidy(plan(*terms))
    done: list[str] = []
    ours = review_plan(p, profile(done=done), CATALOG)
    theirs = validate_plan_text(plan_text(p), done, CATALOG)
    assert (ours["status"] != "errors") == theirs["valid"]


# ---- timetable ----

TT = TimetableAdapter(SampleFileSource())
W = "2027 Winter"


def sections(*ids):
    found = {s.section_id: s for c in ("MAT224H5", "MAT244H5", "STA256H5") for s in TT.search_sections(c, W)}
    return [found[i] for i in ids]


def test_minutes_and_clash_detection_on_sample_sections():
    assert minutes("13:30") == 13 * 60 + 30
    clashes = find_clashes(sections("MAT244H5-20271-LEC0101", "STA256H5-20271-LEC0101"))
    assert {(c["day"], c["start"], c["end"]) for c in clashes} == {
        ("Tuesday", "14:00", "16:00"),
        ("Thursday", "14:00", "15:00"),
    }
    assert find_clashes(sections("MAT224H5-20271-LEC0101", "MAT244H5-20271-LEC0101")) == []  # back-to-back or apart


def test_touching_sections_do_not_clash():
    a, b = sections("MAT224H5-20271-LEC0102", "MAT244H5-20271-LEC0101")  # Tue 15-17 vs Tue 14-16: overlap
    assert len(find_clashes([a, b])) == 1


def test_one_click_fix_switches_to_the_section_that_resolves_the_clash():
    view = timetable_view(TT, ["MAT224H5", "MAT244H5"], W, ["MAT224H5-20271-LEC0102", "MAT244H5-20271-LEC0101"])
    assert len(view["clashes"]) == 1 and view["clashes"][0]["day"] == "Tuesday"
    fix = view["fixes"][0]
    assert (fix["from"], fix["to"], fix["remaining"]) == ("MAT224H5-20271-LEC0102", "MAT224H5-20271-LEC0101", 0)
    assert (
        "overlap on Tuesday from 15:00 to 16:00" in view["explanation"]
        and "Switching MAT224H5 to LEC0101 fixes it" in view["explanation"]
    )
    applied = timetable_view(TT, ["MAT224H5", "MAT244H5"], W, [fix["to"], "MAT244H5-20271-LEC0101"])
    assert applied["clashes"] == [] and applied["explanation"].startswith("No clashes")


def test_a_clash_with_no_alternative_is_explained_without_inventing_a_fix():
    view = timetable_view(TT, ["MAT244H5", "STA256H5"], W, [])
    assert view["clashes"] and view["fixes"] == []
    assert "No other section" in view["explanation"]


def test_courses_without_sample_sections_are_marked_unavailable_and_the_data_is_labelled():
    view = timetable_view(TT, ["MAT224H5", "MAT401H5"], W, [])
    assert [c["available"] for c in view["courses"]] == [True, False]
    assert view["data_source"].startswith("sample (synthetic")
    assert suggest_fixes([], {}) == []


def test_default_selection_picks_one_section_per_type():
    view = timetable_view(TT, ["MAT224H5"], W, [])
    assert [c["section_id"] for c in view["chosen"]] == ["MAT224H5-20271-LEC0101"]


# ---- API ----


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


def H(user):
    return {"Authorization": f"Bearer {user}"}


def test_plan_lifecycle_review_on_every_change_and_isolation(client):
    h = H("ana")
    client.put(
        "/api/profile", json={"courses": [{"code": "MAT102H5", "status": "done"}], "program": "ERMAJ2511"}, headers=h
    )
    created = client.post(
        "/api/plans", json={"name": "Plan A", "terms": [{"term": "2026 Fall", "courses": ["MAT224H5"]}]}, headers=h
    )
    assert created.status_code == 200
    body = created.json()
    assert body["review"]["status"] == "errors"  # MAT224H5 needs MAT223H5 first

    moved = client.put(
        f"/api/plans/{body['id']}",
        json={
            "name": "Plan A",
            "terms": [{"term": "2026 Fall", "courses": ["MAT223H5"]}, {"term": "2027 Winter", "courses": ["MAT224H5"]}],
        },
        headers=h,
    ).json()
    assert not [i for i in moved["review"]["items"] if i["level"] == "error"]

    dup = client.post(f"/api/plans/{body['id']}/duplicate", json={}, headers=h).json()
    assert dup["name"] == "Plan B" and dup["id"] != body["id"] and dup["terms"] == moved["terms"]
    assert len(client.get("/api/plans", headers=h).json()["plans"]) == 2
    assert client.get("/api/plans", headers=H("ben")).json()["plans"] == []  # another student sees nothing
    assert client.put(f"/api/plans/{body['id']}", json={"name": "x", "terms": []}, headers=H("ben")).status_code == 404

    assert (
        client.put(
            f"/api/plans/{body['id']}",
            json={"name": "A", "terms": [{"term": "2026 Fall", "courses": ["ZZZ999H5"]}]},
            headers=h,
        ).status_code
        == 422
    )
    assert (
        client.put(
            f"/api/plans/{body['id']}", json={"name": "A", "terms": [{"term": "Fall 26", "courses": []}]}, headers=h
        ).status_code
        == 422
    )
    assert client.delete(f"/api/plans/{dup['id']}", headers=h).json() == {"ok": True}
    assert len(client.get("/api/plans", headers=h).json()["plans"]) == 1


def test_add_course_from_chat_creates_plan_a_then_extends_it(client):
    h = H("ana")
    first = client.post("/api/plans/add-course", json={"code": "MAT223H5"}, headers=h).json()
    assert first["name"] == "Plan A" and first["terms"][0]["courses"] == ["MAT223H5"]
    second = client.post("/api/plans/add-course", json={"code": "mat224h5"}, headers=h).json()
    assert second["id"] == first["id"]
    assert [t["term"] for t in second["terms"] if "MAT224H5" in t["courses"]]  # placed after its prerequisite's term
    placed = {c: t["term"] for t in second["terms"] for c in t["courses"]}
    assert placed["MAT224H5"] > placed["MAT223H5"] or placed["MAT224H5"].split()[0] > placed["MAT223H5"].split()[0]
    assert (
        client.post("/api/plans/add-course", json={"code": "MAT224H5"}, headers=h).status_code == 422
    )  # already there
    assert len(client.get("/api/plans", headers=h).json()["plans"]) == 1


def test_timetable_api_saves_the_chosen_sections_and_reports_clashes(client):
    h = H("ana")
    plan_id = client.post(
        "/api/plans", json={"terms": [{"term": W, "courses": ["MAT224H5", "MAT244H5"]}]}, headers=h
    ).json()["id"]
    view = client.get(f"/api/plans/{plan_id}/timetable", params={"term": W}, headers=h).json()
    assert view["clashes"] == [] and view["data_source"].startswith("sample")
    clash = client.put(
        f"/api/plans/{plan_id}/timetable",
        json={"term": W, "section_ids": ["MAT224H5-20271-LEC0102", "MAT244H5-20271-LEC0101"]},
        headers=h,
    ).json()
    assert len(clash["clashes"]) == 1 and clash["fixes"][0]["to"] == "MAT224H5-20271-LEC0101"
    saved = client.get(f"/api/plans/{plan_id}/timetable", params={"term": W}, headers=h).json()
    assert [c["section_id"] for c in saved["chosen"]] == [c["section_id"] for c in clash["chosen"]]  # remembered


def test_delete_my_data_also_removes_plans(client):
    h = H("ana")
    client.post("/api/plans", json={"terms": []}, headers=h)
    client.delete("/api/profile", headers=h)
    assert client.get("/api/plans", headers=h).json()["plans"] == []


def test_plan_store_is_per_user():
    store = MemoryPlanStore()
    store.put("ana", [Plan(name="A")])
    assert store.get("ben") == [] and store.get("ana")[0].name == "A"
