"""Application configuration.

Loads all settings from the environment (and an optional ``server/.env`` for local
development). Mandatory values:

- ``TZ`` — IANA timezone name used for every local-day boundary.
- ``GUAI_SYNC_TOKEN`` — bearer token for the device-facing endpoints (>= 16 chars).

See ``spec/operations.md`` for the full environment table.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from zoneinfo import ZoneInfo

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_SERVER_DIR = Path(__file__).resolve().parent.parent.parent  # .../server
_DEFAULT_STATIC_DIR = str(_SERVER_DIR.parent / "frontend" / "public")


class Settings(BaseSettings):
    """Server configuration, read from process environment and an optional ``.env`` file."""

    model_config = SettingsConfigDict(
        env_prefix="",
        populate_by_name=True,
        case_sensitive=False,
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- Required -----------------------------------------------------
    tz: str = Field(validation_alias="TZ")
    sync_token: str = Field(min_length=16, validation_alias="GUAI_SYNC_TOKEN")

    # --- Optional -----------------------------------------------------
    db_path: str = Field(default="data/guai.sqlite3", validation_alias="GUAI_DB_PATH")
    weather_interval_minutes: int = Field(
        default=60, ge=5, le=1440, validation_alias="GUAI_WEATHER_INTERVAL_MINUTES"
    )
    static_dir: str = Field(default=_DEFAULT_STATIC_DIR, validation_alias="GUAI_STATIC_DIR")
    migrations_dir: str = Field(default="", validation_alias="GUAI_MIGRATIONS_DIR")
    enable_scheduler: bool = Field(default=True, validation_alias="GUAI_SCHEDULER")
    pushover_token: str | None = Field(default=None, validation_alias="PUSHOVER_TOKEN")
    pushover_user: str | None = Field(default=None, validation_alias="PUSHOVER_USER")

    # --- Validation ---------------------------------------------------
    @field_validator("tz")
    @classmethod
    def _valid_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except Exception as exc:  # pragma: no cover - defensive
            raise ValueError(f"invalid TZ {value!r}: expected an IANA timezone name") from exc
        return value

    @model_validator(mode="after")
    def _pushover_pair(self) -> Settings:
        if bool(self.pushover_token) != bool(self.pushover_user):
            raise ValueError("PUSHOVER_TOKEN and PUSHOVER_USER must be set together")
        return self

    # --- Convenience --------------------------------------------------
    @property
    def timezone(self) -> ZoneInfo:
        return ZoneInfo(self.tz)

    @property
    def sqlite_url(self) -> str:
        return f"sqlite:///{Path(self.db_path)}"

    @property
    def sqlite_async_url(self) -> str:
        return f"sqlite+aiosqlite:///{Path(self.db_path)}"


@lru_cache
def get_settings() -> Settings:
    return Settings()
