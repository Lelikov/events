from functools import lru_cache

from pydantic import Field, PostgresDsn, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    debug: bool = False
    log_level: str = "INFO"

    # asyncpg URL, e.g. postgresql+asyncpg://event_scheduling:event_scheduling@postgres:5432/event_scheduling
    postgres_dsn: PostgresDsn = Field(strict=True)

    # Static bearer key gating every /api/* route (constant-time compared in auth.py).
    scheduling_api_key: str = Field(...)

    # Outbox dispatch (slice 4a): booking-lifecycle CloudEvents published to event-receiver,
    # with participant emails resolved via event-users.
    event_receiver_url: str = "http://event-receiver:8888"
    booking_api_key: str = "dev-booking-api-key"
    event_users_url: str = "http://event-users:8888"
    event_users_token: str = "dev-admin-token"  # noqa: S105 - dev default, real value comes from env/Vault
    outbox_dispatch_interval: float = 5.0
    outbox_batch_size: int = 50
    outbox_max_backoff_seconds: int = 300

    # Reminders (slice 4a.3): in-service poller emits one ~1h-before reminder per confirmed booking.
    reminder_enabled: bool = True
    reminder_interval_seconds: float = 60.0
    reminder_shift_from_minutes: int = 55
    reminder_shift_to_minutes: int = 65
    reminder_batch_size: int = 100

    @field_validator("log_level")
    @classmethod
    def validate_log_level(cls, v: str) -> str:
        valid_levels = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
        upper = v.upper()
        if upper not in valid_levels:
            raise ValueError(f"Invalid log_level: {v!r}. Must be one of {sorted(valid_levels)}")
        return upper


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
