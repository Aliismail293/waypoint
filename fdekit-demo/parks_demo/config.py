from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="PARKS_")

    llm: str = "mock"  # mock | vertex
    source: str = "csv"  # csv | postgres
    gcp_project: str = "waypoint-513003"
    model_location: str = "global"
    model: str = "gemini-3.5-flash"
    db_host: str = "127.0.0.1"
    db_port: int = 5432
    db_name: str = "waypoint"  # the existing instance and database, with a separate schema
    db_user: str = "postgres"
    db_password: str = ""
    db_schema: str = "fdekit_demo"
    max_model_calls: int = 8
    max_requests_per_user_per_day: int = 20
    google_client_id: str = ""


@lru_cache
def get_settings() -> Settings:
    return Settings()
