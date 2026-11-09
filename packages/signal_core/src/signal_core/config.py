"""Typed settings read from the environment (S8: secrets never live in code)."""

from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_env: Literal["local", "staging", "production", "test"] = "local"
    log_level: str = "info"
    port: int = 8080

    # Session pooler / direct connection: required for LISTEN/NOTIFY and Procrastinate.
    database_url_session: SecretStr | None = None

    anthropic_api_key: SecretStr | None = None
    voyage_api_key: SecretStr | None = None
    ai_model_extract: str = "claude-sonnet-5-5"
    ai_model_judge: str = "claude-sonnet-5-5"
    ai_model_prejudge: str = "claude-haiku-5-5"
    ai_model_write: str = "claude-sonnet-5-5"
    ai_model_embed: str = "voyage-4"

    sentry_dsn: SecretStr | None = None

    # Supabase project URL (same as NEXT_PUBLIC_SUPABASE_URL) and service key —
    # used by the worker to download uploaded files from Supabase Storage.
    supabase_url: str | None = None
    supabase_service_role_key: SecretStr | None = None

    outbox_poll_seconds: float = Field(default=5.0, gt=0)
    outbox_batch_size: int = Field(default=100, gt=0, le=1000)


@lru_cache
def get_settings() -> Settings:
    return Settings()
