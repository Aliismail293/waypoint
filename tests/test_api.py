import pytest
from fastapi.testclient import TestClient

from waypoint.agents.runtime import WaypointRuntime
from waypoint.api import auth
from waypoint.api.app import create_app
from waypoint.config import Settings

OFFLINE = dict(llm="mock", embeddings="mock", store="memory", catalog="memory", sessions="memory")


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
