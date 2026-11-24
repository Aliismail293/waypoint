import pytest
from fastapi.testclient import TestClient

from waypoint.agents.runtime import WaypointRuntime
from waypoint.api import auth
from waypoint.api.app import create_app
from waypoint.config import Settings

# fact_route off: these tests exercise the agent/router/stream paths; the fact route has tests/test_facts.py
OFFLINE = dict(llm="mock", embeddings="mock", store="memory", catalog="memory", sessions="memory", fact_route="off")


def client_for(**overrides) -> TestClient:
    settings = Settings(**(OFFLINE | overrides))
    app = create_app(settings, WaypointRuntime.from_settings(settings))
    return TestClient(app)


def fake_claims(aud: str, email: str = "student@mail.utoronto.ca", sub: str = "123"):
    return {"iss": "https://accounts.google.com", "aud": aud, "email": email, "email_verified": True, "sub": sub}


def test_static_and_config():
    with client_for(google_client_id="cid.apps.googleusercontent.com") as client:
        assert "Waypoint" in client.get("/").text
        assert client.get("/api/config").json()["google_client_id"] == "cid.apps.googleusercontent.com"
        assert client.get("/healthz").json() == {"ok": True}


@pytest.mark.parametrize(("path", "heading"), [("/privacy", "Privacy policy"), ("/terms", "Terms of use")])
def test_legal_pages_are_public(path, heading):
    # Sign-in is configured, but no token is sent: these pages must still load (Google requires them).
    with client_for(google_client_id="cid") as client:
        response = client.get(path)
        assert response.status_code == 200
        assert heading in response.text and "student project" in response.text
        assert f'href="{path}"' in client.get("/").text  # linked from the chat UI footer


def test_chat_fails_closed_without_client_id():
    with client_for(google_client_id="") as client:
        assert client.post("/api/chat", json={"message": "hi"}).status_code == 503


def test_chat_requires_token():
    with client_for(google_client_id="cid") as client:
        assert client.post("/api/chat", json={"message": "hi"}).status_code == 401


def test_chat_with_valid_token_returns_cited_answer(monkeypatch):
    monkeypatch.setattr(auth.id_token, "verify_oauth2_token", lambda token, req, audience=None: fake_claims("cid"))
    with client_for(google_client_id="cid") as client:
        r = client.post(
            "/api/chat",
            json={"message": "What are the prerequisites for MAT224?"},
            headers={"Authorization": "Bearer t"},
        )
        assert r.status_code == 200
        body = r.json()
        assert body["citations"][0]["chunk_id"] == "cal:mathematical-sciences#mat224h5"
        assert body["citations"][0]["url"].startswith("https://utm.calendar.utoronto.ca/")
        assert body["metrics"]["model_calls"] >= 2


@pytest.mark.parametrize(
    ("aud", "email", "status"),
    [
        ("someone-else", "student@mail.utoronto.ca", 401),
        (auth.GCLOUD_CLI_CLIENT_ID, "stranger@gmail.com", 401),  # gcloud tokens only for operators
        (auth.GCLOUD_CLI_CLIENT_ID, "op@example.com", 200),
    ],
)
def test_audience_rules(monkeypatch, aud, email, status):
    monkeypatch.setattr(auth.id_token, "verify_oauth2_token", lambda token, req, audience=None: fake_claims(aud, email))
    with client_for(google_client_id="cid", operator_emails="op@example.com") as client:
        r = client.post("/api/chat", json={"message": "What is MAT301?"}, headers={"Authorization": "Bearer t"})
        assert r.status_code == status


def test_daily_limit(monkeypatch):
    monkeypatch.setattr(auth.id_token, "verify_oauth2_token", lambda token, req, audience=None: fake_claims("cid"))
    with client_for(google_client_id="cid", max_requests_per_user_per_day=1) as client:
        headers = {"Authorization": "Bearer t"}
        assert client.post("/api/chat", json={"message": "MAT301?"}, headers=headers).status_code == 200
        assert client.post("/api/chat", json={"message": "MAT301?"}, headers=headers).status_code == 429


def test_dev_skip_auth_is_ignored_on_cloud_run(monkeypatch):
    monkeypatch.setenv("K_SERVICE", "waypoint")
    with client_for(google_client_id="cid", dev_skip_auth=True) as client:
        assert client.post("/api/chat", json={"message": "hi"}).status_code == 401


def test_message_length_is_capped():
    with client_for(dev_skip_auth=True) as client:
        assert client.post("/api/chat", json={"message": "x" * 2001}).status_code == 422


def test_feedback_escalation_and_operator_stats(monkeypatch):
    from waypoint.analytics import MemoryUsageStore

    claims = {"value": fake_claims("cid", email="student@mail.utoronto.ca", sub="s1")}
    monkeypatch.setattr(auth.id_token, "verify_oauth2_token", lambda token, req, audience=None: claims["value"])
    settings = Settings(**(OFFLINE | {"google_client_id": "cid", "operator_emails": "op@example.com"}))
    usage = MemoryUsageStore()
    app = create_app(settings, WaypointRuntime.from_settings(settings), usage)
    headers = {"Authorization": "Bearer t"}
    with TestClient(app) as client:
        chat = client.post("/api/chat", json={"message": "What does MAT224 unlock?"}, headers=headers).json()
        ids = {"session_id": chat["session_id"], "answer_id": chat["answer_id"]}
        assert client.post("/api/feedback", json=ids | {"rating": -1}, headers=headers).json() == {"ok": True}
        assert client.post("/api/feedback", json=ids | {"rating": 1}, headers=headers).status_code == 200
        assert client.post("/api/feedback", json=ids | {"rating": 5}, headers=headers).status_code == 422
        assert client.post("/api/escalate", json=ids, headers=headers).json()["advisor_url"].startswith("https://")
        assert client.get("/api/stats", headers=headers).status_code == 403  # students can't read stats

        claims["value"] = fake_claims("cid", email="op@example.com", sub="op")
        stats = client.get("/api/stats", headers=headers).json()
    assert stats["users"] == 1 and stats["questions"] == 1
    assert stats["rated_answers"] == 1 and stats["thumbs_up_rate"] == 1.0  # latest click wins
    assert stats["escalation_rate"] == 1.0
    assert all("s1" not in e.user and "MAT224" not in str(e) for e in usage.all())  # no raw ids, no text


def test_source_reader_returns_text_highlight_and_official_url(monkeypatch):
    monkeypatch.setattr(auth.id_token, "verify_oauth2_token", lambda token, req, audience=None: fake_claims("cid"))
    headers = {"Authorization": "Bearer t"}
    with client_for(google_client_id="cid") as client:
        chunk_id = "cal:mathematical-sciences#mat224h5"
        assert client.post("/api/source", json={"chunk_id": chunk_id}).status_code == 401
        r = client.post("/api/source", json={"chunk_id": chunk_id, "claim": "MAT224H5 prerequisites"}, headers=headers)
        assert r.status_code == 200
        body = r.json()
        assert body["code"] == "MAT224H5" and body["url"].startswith("https://utm.calendar.utoronto.ca/")
        assert body["breadcrumb"][0] == "Academic Calendar"
        if body["highlight"]:  # the span must index the returned text
            assert 0 <= body["highlight"]["start"] < body["highlight"]["end"] <= len(body["text"])
        missing = client.post("/api/source", json={"chunk_id": "cal:nope#nope"}, headers=headers)
        assert missing.status_code == 404
        assert client.post("/api/source", json={"chunk_id": "'; drop"}, headers=headers).status_code == 422


def test_source_mismatch_feedback_is_recorded_without_text(monkeypatch):
    monkeypatch.setattr(auth.id_token, "verify_oauth2_token", lambda token, req, audience=None: fake_claims("cid"))
    settings = Settings(**(OFFLINE | {"google_client_id": "cid"}))
    from waypoint.analytics import MemoryUsageStore

    usage = MemoryUsageStore()
    app = create_app(settings, WaypointRuntime.from_settings(settings), usage)
    with TestClient(app) as client:
        r = client.post(
            "/api/source-feedback",
            json={"chunk_id": "cal:mathematical-sciences#mat224h5", "session_id": "s", "answer_id": "a"},
            headers={"Authorization": "Bearer t"},
        )
        assert r.json() == {"ok": True}
    (event,) = usage.events
    assert event.type == "source_mismatch" and event.chunk_id.endswith("#mat224h5") and event.user != "123"


def test_config_exposes_dev_mode_only_off_cloud_run():
    with client_for(dev_skip_auth=True) as client:
        assert client.get("/api/config").json()["dev_mode"] is True
    with client_for() as client:
        assert client.get("/api/config").json()["dev_mode"] is False
