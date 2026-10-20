"""Request/response schemas for the HTTP API (pydantic v2)."""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


def parse_iso(value: str, field_name: str) -> datetime:
    try:
        return datetime.fromisoformat(value)
    except ValueError as exc:  # pragma: no cover - defense
        raise ValueError(f"{field_name} must be an ISO-8601 timestamp") from exc


# --- Watch sync ---------------------------------------------------------


class ActivityPayload(BaseModel):
    steps: int | None = None
    distance_meters: float | None = None
    active_seconds: int | None = None
    resting_kcal: float | None = None
    active_kcal: float | None = None


class HeartRateSample(BaseModel):
    at: str
    bpm: int = Field(ge=20, le=250)
    bpm_raw: int | None = Field(default=None, ge=20, le=250)

    @field_validator("at")
    @classmethod
    def _valid_at(cls, value: str) -> str:
        parse_iso(value, "at")
        return value


class SleepSample(BaseModel):
    night_date: str
    sleep_seconds: int | None = None
    restful_seconds: int | None = None

    @field_validator("night_date")
    @classmethod
    def _valid_night_date(cls, value: str) -> str:
        try:
            date.fromisoformat(value)
        except ValueError as exc:  # pragma: no cover - defense
            raise ValueError("night_date must be a YYYY-MM-DD date") from exc
        return value


class SyncPayload(BaseModel):
    collected_at: str | None = None
    device: str | None = None
    activity: ActivityPayload | None = None
    heart_rates: list[HeartRateSample] | None = None
    sleep: list[SleepSample] | None = None

    @field_validator("collected_at")
    @classmethod
    def _valid_collected_at(cls, value: str | None) -> str | None:
        if value is not None:
            parse_iso(value, "collected_at")
        return value

    @model_validator(mode="after")
    def _activity_needs_collected_at(self) -> SyncPayload:
        if self.activity is not None and self.collected_at is None:
            raise ValueError("collected_at is required when activity is present")
        return self


class SyncResponse(BaseModel):
    server_time: str
    next_poll_seconds: int


# --- Dashboard config ---------------------------------------------------


class LocationInput(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)


class GeoResult(BaseModel):
    name: str
    latitude: float
    longitude: float
    country: str | None = None
    admin1: str | None = None


class ConfigResponse(BaseModel):
    location: LocationResult | None = None
    server_version: str


class LocationResult(BaseModel):
    name: str
    latitude: float
    longitude: float


# --- Check-ins -------------------------------------------------------------


class Schedule(BaseModel):
    """Check-in schedule. See spec/data-and-api.md.

    - daily: ``{"repeat": "daily", "times": ["09:00", "21:05"]}``
    - weekly: ``{"repeat": "weekly", "days": [1..7], "times": ["08:30"]}``
    - once: ``{"repeat": "once", "scheduled_at": "2026-10-01T09:00"}`` (local, in TZ)
    """

    model_config = ConfigDict(extra="forbid")

    repeat: Literal["daily", "weekly", "once"]
    times: list[str] | None = None
    days: list[int] | None = None
    scheduled_at: str | None = None

    @model_validator(mode="after")
    def _validate(self) -> Schedule:
        if self.repeat in ("daily", "weekly"):
            if not self.times:
                raise ValueError("times is required for daily/weekly schedules")
            self.times = _validated_times(self.times)
            if self.repeat == "weekly":
                if not self.days or any(d < 1 or d > 7 for d in self.days):
                    raise ValueError("days must be a non-empty subset of 1-7 for weekly")
                self.days = sorted(set(self.days))
            else:
                self.days = None
            self.scheduled_at = None
        else:  # once
            try:
                datetime.strptime(self.scheduled_at or "", "%Y-%m-%dT%H:%M")
            except ValueError as exc:  # pragma: no cover - defensive
                raise ValueError("scheduled_at must be 'YYYY-MM-DDTHH:MM' (local time)") from exc
            self.times = None
            self.days = None
        return self


class CheckinWrite(BaseModel):
    """Payload for creating/updating a check-in definition."""

    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=200)
    answer_type: Literal["boolean", "scale", "single_choice"]
    scale_min: int = Field(default=0)
    scale_max: int = Field(default=10)
    options: list[str] | None = None
    schedule: Schedule
    due_window_minutes: int = Field(default=60, ge=5, le=1440)
    reminder_minutes: int = Field(default=0)

    @model_validator(mode="after")
    def _validate(self) -> CheckinWrite:
        if self.reminder_minutes != 0 and not (10 <= self.reminder_minutes <= 1440):
            raise ValueError("reminder_minutes must be 0 (off) or 10-1440")
        if self.scale_min >= self.scale_max:
            raise ValueError("scale_min must be less than scale_max")
        if self.answer_type == "scale":
            if self.options:
                raise ValueError("scale check-ins do not take options")
        elif self.answer_type == "single_choice":
            if not self.options or len(self.options) < 2:
                raise ValueError("single_choice requires at least two options")
            if any(not o.strip() for o in self.options):
                raise ValueError("options must be non-empty strings")
        else:  # boolean
            if self.options:
                raise ValueError("boolean check-ins do not take options")
            if self.scale_min != 0 or self.scale_max != 10:
                self.scale_min, self.scale_max = 0, 10
        return self


class AnswerPayload(BaseModel):
    """One answer; exactly one of boolean/scale/choice is set. Semantics are
    re-validated against the definition at submission time."""

    model_config = ConfigDict(extra="forbid")

    boolean: bool | None = None
    scale: int | None = None
    choice: str | None = None

    @model_validator(mode="after")
    def _validate(self) -> AnswerPayload:
        present = sum(v is not None for v in (self.boolean, self.scale, self.choice))
        if present != 1:
            raise ValueError("exactly one of boolean, scale, choice must be set")
        return self


def _validated_times(times: list[str]) -> list[str]:
    result = sorted(set(times))
    for t in result:
        try:
            datetime.strptime(t, "%H:%M")
        except ValueError as exc:  # pragma: no cover - defensive
            raise ValueError(f"times entries must be HH:MM, got {t!r}") from exc
    return result
