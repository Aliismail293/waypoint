"""History (record, search, filters, pins) and the advisor meeting pack (PDF and email), all offline."""

import pytest
from fastapi.testclient import TestClient

from waypoint.agents.runtime import WaypointRuntime
from waypoint.api import auth
from waypoint.api.app import create_app
from waypoint.config import Settings
from waypoint.history import MAX_TURNS, SourceRef, Turn, matches, record, topic_of
from waypoint.pack import Pack, build_pack, latin1, pack_email, pack_pdf, plain

# fact_route off: these tests exercise the agent/router/stream paths; the fact route has tests/test_facts.py
OFFLINE = dict(llm="mock", embeddings="mock", store="memory", catalog="memory", sessions="memory", fact_route="off")
URL = "https://utm.calendar.utoronto.ca/course/MAT224H5"


def turn(i="a1", q="What are the prerequisites for MAT224H5?", a="MAT223H5 [cal:m#mat224h5]", **kw) -> Turn:
    return Turn(
        id=i,
        question=q,
        answer=a,
        sources=[SourceRef(chunk_id="cal:m#mat224h5", heading="Math > MAT224H5 Linear Algebra II", url=URL)],
        **kw,
    )


# ---- history ----


def test_topics_and_filters():
    assert topic_of(turn(category="planning")) == "plans" and topic_of(turn(category="timetable")) == "timetable"
    assert topic_of(turn()) == "prerequisites"
    assert topic_of(turn(q="How many credits is the major?", a="8.5 credits")) == "other"
    pinned = turn(pinned=True)
    assert matches(pinned, "", "pinned") and not matches(turn(), "", "pinned")
    assert matches(turn(), "LINEAR", "all") is False and matches(turn(a="Linear algebra"), "linear", "all")
    assert matches(turn(), "mat224", "prerequisites") and not matches(turn(), "", "plans")


def test_record_is_newest_first_deduplicates_and_keeps_pins_past_the_cap():
    turns: list[Turn] = []
    for i in range(MAX_TURNS + 10):
        turns = record(turns, turn(i=f"t{i}", pinned=(i == 0)))
    assert len(turns) == MAX_TURNS and turns[0].id == f"t{MAX_TURNS + 9}"
    assert any(t.id == "t0" and t.pinned for t in turns)  # the oldest turn survives because it is pinned
    assert [t.id for t in record([turn(i="x"), turn(i="y")], turn(i="y"))] == ["y", "x"]


# ---- pack: text helpers, PDF, email ----


def test_text_helpers():
    assert latin1("MAT224H5 – “Linear” ✓ · 日本") == 'MAT224H5 - "Linear" [ok] · ??'
    assert plain("**Prereq** [cal:m#a][cal:m#b]\n* item [cal:m#c]") == "Prereq\n- item"


def sample_plan():
    return {
        "name": "Plan A",
        "terms": [{"term": "2026 Fall", "courses": ["MAT223H5"]}, {"term": "2027 Winter", "courses": ["MAT224H5"]}],
        "titles": {"MAT223H5": "Linear Algebra I", "MAT224H5": "Linear Algebra II"},
        "review": {
            "credits": {"counted": 1.0, "planned": 1.0, "required": [8.5, 9.0]},
            "items": [
                {"level": "ok", "text": "fine", "chunk_ids": [], "codes": []},
                {
                    "level": "warn",
                    "text": "1 credits counted so far. Needs 8.5–9.",
                    "chunk_ids": ["cal:m#erm"],
                    "codes": [],
                },
            ],
        },
        "sources": [
            {
                "chunk_id": "cal:m#erm",
                "code": "",
                "title": "Program",
                "url": "https://utm.calendar.utoronto.ca/program/ERMAJ2511",
            }
        ],
    }


def sample_pack(**over) -> Pack:
    kw = dict(
        profile={"program": "ERMAJ2511", "grad_year": 2029},
        plan=sample_plan(),
        turns=[turn(pinned=True)],
        timetable={
            "term": "2027 Winter",
            "chosen": [
                {
                    "course": "MAT224H5",
                    "type": "Lecture",
                    "number": "0101",
                    "meetings": [{"day": "Wednesday", "start": "11:00", "end": "13:00"}],
                }
            ],
            "explanation": "No clashes: none of your chosen sections overlap.",
        },
        include_critic=True,
    )
    return build_pack(**(kw | over))


def test_pdf_builds_and_carries_citations_links_and_the_required_notes():
    data = pack_pdf(sample_pack(), compress=False)
    assert data.startswith(b"%PDF") and data.rstrip().endswith(b"%%EOF")
    text = data.decode("latin-1")
    for expected in (
        "Plan: Plan A",
        "MAT224H5 Linear Algebra II",
        "ERMAJ2511",
        "SAMPLE DATA",
        "not affiliated with U of T",
    ):
        assert expected in text
    assert f"/URI ({URL})" in text  # the pinned answer's real calendar link
    assert "/URI (https://utm.calendar.utoronto.ca/program/ERMAJ2511)" in text  # and the critic note's
    assert "cal:m#" not in text  # raw chunk markers never reach the page


def test_pdf_is_valid_when_compressed_and_when_sections_are_empty():
    assert pack_pdf(sample_pack()).startswith(b"%PDF")
    empty = build_pack(profile={}, plan=None, turns=[], timetable=None, include_critic=False)
    assert pack_pdf(empty).startswith(b"%PDF")


def test_pack_respects_the_checkboxes_and_caps_pinned_answers():
    without = sample_pack(include_critic=False, turns=[], timetable=None)
    assert not without.notes and not without.answers and without.timetable is None
    many = sample_pack(turns=[turn(i=f"t{i}") for i in range(8)])
    assert len(many.answers) == 5 and many.answers_omitted == 3


def test_email_text_has_the_plan_sources_and_the_disclaimers():
    text = pack_email(sample_pack())
    assert "Plan A:" in text and "- 2027 Winter: MAT224H5 Linear Algebra II" in text
    assert URL in text and "sample data" in text and "not affiliated with U of T" in text


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


def ask(client, user, question):
    return client.post("/api/chat", json={"message": question}, headers=H(user)).json()


def test_answers_are_recorded_searchable_pinnable_and_private(client):
    a = ask(client, "ana", "What are the prerequisites for MAT224H5? My student number is 1002345678")
    ask(client, "ben", "What are the prerequisites for MAT344H5?")
    history = client.get("/api/history", headers=H("ana")).json()
    assert history["total"] == 1 and history["turns"][0]["id"] == a["answer_id"]
    assert "1002345678" not in history["turns"][0]["question"]  # redacted before it is stored
    assert history["turns"][0]["sources"][0]["url"].startswith("https://utm.calendar")
    assert client.get("/api/history", params={"q": "MAT344"}, headers=H("ana")).json()["turns"] == []  # not ben's
    assert client.get("/api/history", params={"q": "mat224"}, headers=H("ana")).json()["total"] == 1
    assert client.get("/api/history", params={"filter": "bogus"}, headers=H("ana")).status_code == 422

    assert client.get("/api/history", params={"filter": "pinned"}, headers=H("ana")).json()["turns"] == []
    assert client.post(f"/api/history/{a['answer_id']}/pin", json={"pinned": True}, headers=H("ana")).json()["pinned"]
    assert len(client.get("/api/history", params={"filter": "pinned"}, headers=H("ana")).json()["turns"]) == 1
    assert client.post(f"/api/history/{a['answer_id']}/pin", json={"pinned": True}, headers=H("ben")).status_code == 404

    client.delete("/api/profile", headers=H("ana"))  # "Delete my data" clears history too
    assert client.get("/api/history", headers=H("ana")).json()["total"] == 0


def test_streamed_answers_are_recorded_too(client):
    with client.stream(
        "POST", "/api/chat/stream", json={"message": "What are the prerequisites for MAT224H5?"}, headers=H("ana")
    ) as r:
        body = "".join(r.iter_text())
    assert "event: done" in body
    assert client.get("/api/history", headers=H("ana")).json()["total"] == 1


def test_pack_pdf_and_email_contain_only_the_students_own_data(client):
    secret = ask(client, "ben", "What are the prerequisites for MAT344H5?")
    client.post(f"/api/history/{secret['answer_id']}/pin", json={"pinned": True}, headers=H("ben"))
    mine = ask(client, "ana", "What are the prerequisites for MAT224H5?")
    client.post(f"/api/history/{mine['answer_id']}/pin", json={"pinned": True}, headers=H("ana"))
    plan = client.post(
        "/api/plans", json={"terms": [{"term": "2027 Winter", "courses": ["MAT224H5"]}]}, headers=H("ana")
    ).json()
    ben_plan = client.post(
        "/api/plans",
        json={"name": "Ben secret", "terms": [{"term": "2026 Fall", "courses": ["MAT202H5"]}]},
        headers=H("ben"),
    ).json()

    body = {"plan_id": plan["id"], "timetable_term": "2027 Winter"}
    pdf = client.post("/api/pack/pdf", json=body, headers=H("ana"))
    assert (
        pdf.status_code == 200 and pdf.headers["content-type"] == "application/pdf" and pdf.content.startswith(b"%PDF")
    )
    email = client.post("/api/pack/email", json=body, headers=H("ana")).json()["text"]
    assert "MAT224H5" in email and "MAT344H5" not in email and "Ben secret" not in email and "MAT202H5" not in email
    assert "sample data" in email  # the timetable section is labelled
    # another student's plan can't be used
    assert client.post("/api/pack/pdf", json={"plan_id": ben_plan["id"]}, headers=H("ana")).status_code == 404
    assert (
        client.post("/api/pack/email", json={"include_pinned": False}, headers=H("ana")).json()["text"].count("Q:") == 0
    )


def test_cors_is_off_by_default_and_limited_to_the_listed_origins():
    def preflight(cors):
        settings = Settings(**OFFLINE, google_client_id="cid", cors_origins=cors)
        with TestClient(create_app(settings, WaypointRuntime.from_settings(settings))) as c:
            return c.options(
                "/api/history",
                headers={"Origin": "https://site.web.app", "Access-Control-Request-Method": "GET"},
            )

    assert "access-control-allow-origin" not in preflight("").headers
    assert preflight("https://site.web.app").headers["access-control-allow-origin"] == "https://site.web.app"
    assert "access-control-allow-origin" not in preflight("https://other.web.app").headers
