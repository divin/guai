"""SQLModel table definitions — schema v1.

Matches ``spec/data-and-api.md``. Conventions:
- Timestamps are ISO-8601 UTC text; local dates are ``YYYY-MM-DD`` text.
- Missing values are ``NULL``; ``0`` means "measured zero".
- Every insertable table has a natural unique key (idempotent upserts).

All constraints (CHECK/UNIQUE/index) are declared here and enforced by SQLite.
"""

from __future__ import annotations

from uuid import uuid4

from sqlalchemy import CheckConstraint, Column, Index, Integer, String, UniqueConstraint
from sqlmodel import Field, SQLModel


def _uuid() -> str:
    return str(uuid4())


class KeyValueSetting(SQLModel, table=True):
    """Key/value server settings editable from the dashboard (location, ...)."""

    __tablename__ = "settings"

    key: str = Field(primary_key=True)
    value: str


class Activity(SQLModel, table=True):
    """Cumulative daily activity counters, one row per poll."""

    __tablename__ = "activity"

    event_time: str = Field(primary_key=True)  # UTC poll time
    steps: int | None = None
    distance_meters: float | None = None
    resting_kcal: float | None = None
    active_kcal: float | None = None
    active_seconds: int | None = None


class HeartRate(SQLModel, table=True):
    """Point-in-time heart-rate samples."""

    __tablename__ = "heart_rate"

    event_time: str = Field(primary_key=True)  # UTC sample time
    bpm: int = Field(
        sa_column=Column(Integer, CheckConstraint("bpm BETWEEN 20 AND 250", name="ck_hr_bpm"))
    )
    bpm_raw: int | None = None


class Sleep(SQLModel, table=True):
    """One row per night, keyed by the local date of the night."""

    __tablename__ = "sleep"

    night_date: str = Field(primary_key=True)
    sleep_seconds: int | None = None
    restful_seconds: int | None = None


class Weather(SQLModel, table=True):
    """Current conditions from Open-Meteo, one row per poll bucket."""

    __tablename__ = "weather"

    event_time: str = Field(primary_key=True)  # UTC, floored to the poll grid
    temperature_c: float | None = None
    humidity_pct: float | None = None
    wind_speed_mps: float | None = None
    precipitation_mm: float | None = None
    pressure_hpa: float | None = None
    weather_code: int | None = None
    condition: str | None = None


class CheckinDefinition(SQLModel, table=True):
    """One user-defined question with its own schedule and answer window."""

    __tablename__ = "checkin_definitions"
    __table_args__ = (
        CheckConstraint("scale_min < scale_max", name="ck_def_scale_range"),
        CheckConstraint("due_window_minutes BETWEEN 5 AND 1440", name="ck_def_due_window"),
        CheckConstraint(
            "reminder_minutes = 0 OR reminder_minutes BETWEEN 10 AND 1440",
            name="ck_def_reminder",
        ),
        Index("ix_checkin_definitions_active_type", "active", "answer_type"),
    )

    id: str = Field(primary_key=True, default_factory=_uuid)
    title: str
    answer_type: str = Field(
        sa_column=Column(
            String,
            CheckConstraint(
                "answer_type IN ('boolean','scale','single_choice')",
                name="ck_def_answer_type",
            ),
        )
    )
    scale_min: int = Field(default=0)
    scale_max: int = Field(default=10)
    options: str | None = None  # JSON array of strings (single_choice)
    schedule: str  # JSON schedule: daily / weekly / once
    due_window_minutes: int = Field(default=60)
    reminder_minutes: int = Field(default=0)
    active: bool = Field(default=True)
    created_at: str  # UTC
    updated_at: str  # UTC


class CheckinInstance(SQLModel, table=True):
    """One scheduled occurrence of a definition: pending -> answered | missed."""

    __tablename__ = "checkin_instances"
    __table_args__ = (
        UniqueConstraint("definition_id", "scheduled_utc", name="uq_inst_def_sched"),
        Index("ix_inst_status", "status"),
        Index("ix_inst_definition_id", "definition_id"),
        CheckConstraint(
            "status IN ('pending','answered','missed')",
            name="ck_inst_status",
        ),
    )

    id: str = Field(primary_key=True, default_factory=_uuid)
    definition_id: str = Field(foreign_key="checkin_definitions.id")
    scheduled_local: str  # local date-time in TZ
    scheduled_utc: str  # derived UTC
    status: str = Field(default="pending")
    pushover_sent_at: str | None = None
    reminder_sent_at: str | None = None
    answered_at: str | None = None
    missed_at: str | None = None


class CheckinResponse(SQLModel, table=True):
    """One response per instance; exactly one value column is set."""

    __tablename__ = "checkin_responses"

    id: str = Field(primary_key=True, default_factory=_uuid)
    instance_id: str = Field(
        foreign_key="checkin_instances.id",
        unique=True,
        index=True,
    )
    value_boolean: int | None = None  # 0/1
    value_scale: int | None = None
    value_choice: str | None = None
    submitted_at: str  # UTC

    __table_args__ = (
        CheckConstraint(
            "(value_boolean IS NOT NULL) + (value_scale IS NOT NULL) + (value_choice IS NOT NULL) = 1",
            name="ck_resp_exactly_one",
        ),
    )
