"""Google Sign-In: verify the ID token the browser got from Google Identity Services.

The browser shows the "Sign in with Google" button (no redirect flow), receives a signed ID token (a JWT)
and sends it as `Authorization: Bearer <token>`. We verify the signature against Google's public keys,
the expiry, the issuer, and that the audience is Waypoint's OAuth client id.

Operator access: a token minted by `gcloud auth print-identity-token` has gcloud's own audience. We accept
it only for emails on WAYPOINT_OPERATOR_EMAILS, so the operator can smoke-test the live service from a
terminal without weakening sign-in for everyone else.
"""

from dataclasses import dataclass

from fastapi import HTTPException, Request
from google.auth.transport import requests as google_requests
from google.oauth2 import id_token

from waypoint.config import Settings

GOOGLE_ISSUERS = {"accounts.google.com", "https://accounts.google.com"}
GCLOUD_CLI_CLIENT_ID = "32555940559.apps.googleusercontent.com"
_transport = google_requests.Request()


@dataclass
class User:
    user_id: str  # Google's stable subject id; never the email
    email: str


def verify_google_token(token: str, settings: Settings) -> User:
    try:
        claims = id_token.verify_oauth2_token(token, _transport, audience=None)
    except ValueError as exc:
        raise HTTPException(status_code=401, detail="Invalid or expired sign-in token.") from exc
    if claims.get("iss") not in GOOGLE_ISSUERS or not claims.get("email_verified"):
        raise HTTPException(status_code=401, detail="Untrusted sign-in token.")
    email = str(claims.get("email", "")).lower()
    audience = claims.get("aud")
    operators = {e.strip().lower() for e in settings.operator_emails.split(",") if e.strip()}
    if audience == settings.google_client_id or (audience == GCLOUD_CLI_CLIENT_ID and email in operators):
        return User(user_id=str(claims["sub"]), email=email)
    raise HTTPException(status_code=401, detail="Sign-in token is for a different application.")


def authenticate(request: Request, settings: Settings) -> User:
    if settings.dev_skip_auth and not settings.on_cloud_run:
        return User(user_id="local-dev", email="dev@localhost")
    if not settings.google_client_id:
        raise HTTPException(status_code=503, detail="Sign-in is not configured on this server.")
    header = request.headers.get("authorization", "")
    if not header.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="Sign in with Google to use Waypoint.")
    return verify_google_token(header.split(" ", 1)[1].strip(), settings)
