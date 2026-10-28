"""All runtime configuration, read from environment variables (prefix WAYPOINT_).

Secrets are never defaults here: the DB password comes from WAYPOINT_DB_PASSWORD, which Cloud Run
injects from Secret Manager and which you export locally from `gcloud secrets versions access`.
"""

import os
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="WAYPOINT_", env_file=".env", extra="ignore")

    # Model provider: "mock" runs fully offline (tests, local dev); "vertex" makes real calls.
    llm: str = "mock"
    embeddings: str = "mock"
    # Data backends: "memory" loads data/corpus/*.jsonl (offline); "pgvector" / "bigquery" use GCP.
    store: str = "memory"
    catalog: str = "memory"

    gcp_project: str = "waypoint-513003"
    gcp_region: str = "us-central1"
    # Gemini 3.5 models are served from the global endpoint on Vertex AI.
    model_location: str = "global"
    agent_model: str = "gemini-3.5-flash"
    critic_model: str = "gemini-3.5-flash"
    embedding_model: str = "gemini-embedding-001"
    embedding_dim: int = 768

    # Postgres (pgvector). Locally via cloud-sql-proxy on 127.0.0.1:5432; on Cloud Run via unix socket.
    db_host: str = "127.0.0.1"
    db_port: int = 5432
    db_name: str = "waypoint"
    db_user: str = "postgres"
    db_password: str = ""
    db_schema: str = "public"

    bq_dataset: str = "waypoint"

    # Budget guards (spec ground rule 5).
    # Thinking tokens count toward max_output_tokens on Gemini 3.x, so leave room for them.
    max_output_tokens: int = 4096
    thinking_level: str = "minimal"
    # Model calls per user request, counted across the orchestrator AND every specialist it calls.
    max_model_calls_per_request: int = 16
    max_requests_per_user_per_day: int = 30

    # Auth. Empty client id = sign-in not configured, and the chat API refuses requests (fail closed).
    google_client_id: str = ""
    # Only honoured when not running on Cloud Run (K_SERVICE unset): lets local dev skip sign-in.
    dev_skip_auth: bool = False
    # gcloud identity tokens are accepted only for these emails (operator smoke tests).
    operator_emails: str = "alii.ekramy.ismail@gmail.com"

    # Observability.
    langfuse_host: str = "https://cloud.langfuse.com"
    langfuse_public_key: str = ""
    langfuse_secret_key: str = ""
    cloud_trace: bool = False
    # Off by default: student questions and answers stay out of trace storage (only metadata and metrics).
    trace_content: bool = False
    service_name: str = "waypoint"

    # Sessions: "memory" (tests/local) or "firestore".
    sessions: str = "memory"

    advisor_url: str = "https://www.utm.utoronto.ca/academic-advising-career-centre/"

    @property
    def on_cloud_run(self) -> bool:
        return bool(os.environ.get("K_SERVICE"))


@lru_cache
def get_settings() -> Settings:
    return Settings()
