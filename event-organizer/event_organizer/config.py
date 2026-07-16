from functools import lru_cache

from pydantic import Field, PostgresDsn
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", case_sensitive=False, extra="ignore")

    debug: bool = False
    log_level: str = "INFO"

    postgres_dsn: PostgresDsn = Field(strict=True)

    # Session JWT
    jwt_secret_key: str = "dev-organizer-jwt-secret"  # noqa: S105 - dev default; real via env/Vault
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 60
    jwt_audience: str | None = None
    jwt_issuer: str | None = None

    # Admin provisioning key (static bearer for POST /admin/organizers)
    organizer_admin_key: str = "dev-organizer-admin-key"

    # Upstreams (server-side keys — never exposed to the browser)
    event_scheduling_url: str = "http://event-scheduling:8888"
    scheduling_api_key: str = "dev-scheduling-api-key-3f9c2e1a7b64d508"
    event_users_url: str = "http://event-users:8888"
    event_users_token: str = "dev-users-bearer-2a7d9e4f8c1b6350"  # noqa: S105 - dev default


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
