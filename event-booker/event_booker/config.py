from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", case_sensitive=False, extra="ignore")

    debug: bool = False
    log_level: str = "INFO"

    # Upstreams (server-side keys — never exposed to the browser).
    event_scheduling_url: str = "http://event-scheduling:8888"
    scheduling_api_key: str = "dev-scheduling-api-key-3f9c2e1a7b64d508"
    event_users_url: str = "http://event-users:8888"
    event_users_token: str = "dev-users-bearer-2a7d9e4f8c1b6350"  # noqa: S105 - dev default; real via env/Vault

    # Comma-separated allowed CORS origins for the public SPA (4b.2). Empty = none (same-origin proxy).
    booker_cors_origins: str = ""

    @property
    def cors_origins_list(self) -> list[str]:
        return [o.strip() for o in self.booker_cors_origins.split(",") if o.strip()]


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
