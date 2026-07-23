"""Application settings, loaded from environment variables (and .env in development).

The database is always external PostgreSQL reached through DATABASE_URL.
Moving from development (own pgAdmin-managed PostgreSQL) to production
(Railway PostgreSQL) means changing only that one variable.
"""
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # Database (external, never Docker)
    database_url: str = ""

    # OpenAI - cloud only, never shipped to the connector
    openai_api_key: str = ""
    openai_model: str = "gpt-4o-mini"

    # Auth
    jwt_secret: str = "development-only-secret-change-me"
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 60
    refresh_token_expire_days: int = 7

    # Bootstrap admin (created on startup if the users table is empty)
    admin_email: str = ""
    admin_password: str = ""

    # Uploads are processed in memory and never stored; this caps the size.
    max_upload_mb: int = 15

    # How long the cloud waits for a connector job result before failing it.
    connector_job_timeout_seconds: int = 60

    # Where the packaged connector .exe is placed for the download button.
    connector_dist_dir: str = "connector_dist"


@lru_cache
def get_settings() -> Settings:
    return Settings()
