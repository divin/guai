"""Daily panel builder for the forecasting module.

Turns the raw sync tables into one ``DailyRow`` per local date, ready for
feature building. This module owns the aggregation rules discovered during EDA
(see PLAN.md) — the single source of truth for daily aggregates, shared by the
forecaster and (from step 2) ``services/insights.py``.

Rules encoded here, each with a test:

- ``steps`` is a **cumulative day counter**: the daily value is the *max*
  within the local day, never a sum (sync bursts repeat the same value).
- A local day is **closed** when its last activity sync is at or after
  ``CLOSURE_HOUR`` (23:00). Unclosed days keep their row (so lag features
  stay calendar-continuous) but carry ``steps=None`` — no target, no
  usable lag value. This is what keeps partial days out of training and
  backtests.
- **Resting HR** is the daily *min* bpm, but only on days with at least
  ``MIN_HR_SAMPLES`` samples; sparse days (watch mostly offline) get ``None``.
- **Sleep** rows arrive keyed by ``night_date`` already — used as-is.
- **Check-ins** are keyed by the *instance's scheduled local day* (an answer
  submitted after midnight still belongs to its scheduled day), and encoded
  as ints: booleans as 0/1, single-choice as the index into ``options``.
- **Weather** contributes a daily mean temperature and rain sum per local day.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date, datetime, time
from zoneinfo import ZoneInfo

from guai_hub.models import (
    Activity,
    CheckinDefinition,
    CheckinInstance,
    CheckinResponse,
    HeartRate,
    Sleep,
    Weather,
)

# A local day counts as closed when its last activity sync is at/after this
# local hour (end-of-day confirmation that the cumulative counter is final).
CLOSURE_HOUR = 23

# Resting HR needs enough samples to trust the daily min; below this the watch
# was offline most of the day and the value is discarded.
MIN_HR_SAMPLES = 100


@dataclass
class DailyRow:
    """One local day of forecast-ready aggregates. ``None`` = not usable."""

    date: date
    dow: int  # 0 = Monday … 6 = Sunday
    day_closed: bool = False
    steps: int | None = None
    sleep_seconds: int | None = None
    resting_bpm: int | None = None
    hr_samples: int = 0
    temp_c: float | None = None
    rain_mm: float | None = None
    checkins: dict[str, int | None] = field(default_factory=dict)


def _to_utc(iso_text: str) -> datetime:
    """Parse an ISO-8601 UTC timestamp (with ``Z`` suffix) into an aware datetime."""
    return datetime.fromisoformat(iso_text)


def _local_date(iso_text: str, tz: ZoneInfo) -> date:
    return _to_utc(iso_text).astimezone(tz).date()


def _local_time(iso_text: str, tz: ZoneInfo) -> time:
    return _to_utc(iso_text).astimezone(tz).time()


def _mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def build_daily_panel(
    *,
    tz: ZoneInfo,
    activity: list[Activity] | None = None,
    heart_rate: list[HeartRate] | None = None,
    sleep: list[Sleep] | None = None,
    weather: list[Weather] | None = None,
    checkins: list[tuple[CheckinDefinition, CheckinInstance, CheckinResponse | None]] | None = None,
) -> list[DailyRow]:
    """Build one ``DailyRow`` per local date that has any data, sorted by date.

    All inputs are optional lists of model rows (fetched by the caller); the
    function is pure so tests can construct rows directly.
    """
    activity = activity or []
    heart_rate = heart_rate or []
    sleep = sleep or []
    weather = weather or []
    checkins = checkins or []

    rows: dict[date, DailyRow] = {}

    def row_for(day: date) -> DailyRow:
        if day not in rows:
            rows[day] = DailyRow(date=day, dow=day.weekday())
        return rows[day]

    # --- activity: cumulative counters, closure rule -----------------------
    events_by_day: dict[date, list[Activity]] = {}
    for event in activity:
        events_by_day.setdefault(_local_date(event.event_time, tz), []).append(event)

    for day, events in events_by_day.items():
        row = row_for(day)
        row.day_closed = max(
            (_local_time(e.event_time, tz) for e in events), default=time.min
        ) >= time(CLOSURE_HOUR, 0)
        steps = [e.steps for e in events if e.steps is not None]
        # Unclosed days carry no steps value: a partial-day max understates the
        # true total and would bias both targets and lag features (EDA finding).
        row.steps = max(steps) if steps and row.day_closed else None

    # --- heart rate: resting = daily min, gated on sample count ------------
    bpm_by_day: dict[date, list[int]] = {}
    for sample in heart_rate:
        bpm_by_day.setdefault(_local_date(sample.event_time, tz), []).append(sample.bpm)
    for day, bpms in bpm_by_day.items():
        row = row_for(day)
        row.hr_samples = len(bpms)
        if len(bpms) >= MIN_HR_SAMPLES:
            row.resting_bpm = min(bpms)

    # --- sleep: already keyed by local night date --------------------------
    for night in sleep:
        if night.sleep_seconds is not None:
            row_for(date.fromisoformat(night.night_date)).sleep_seconds = night.sleep_seconds

    # --- weather: daily mean temp, rain sum --------------------------------
    temps_by_day: dict[date, list[float]] = {}
    rain_by_day: dict[date, list[float]] = {}
    for sample in weather:
        day = _local_date(sample.event_time, tz)
        if sample.temperature_c is not None:
            temps_by_day.setdefault(day, []).append(sample.temperature_c)
        if sample.precipitation_mm is not None:
            rain_by_day.setdefault(day, []).append(sample.precipitation_mm)
    for day, temps in temps_by_day.items():
        row_for(day).temp_c = round(_mean(temps), 2)
    for day, rains in rain_by_day.items():
        row_for(day).rain_mm = round(sum(rains), 2)

    # --- check-ins: keyed by the scheduled local day -----------------------
    for definition, instance, response in checkins:
        if response is None:
            encoded = None
        elif definition.answer_type == "boolean":
            encoded = response.value_boolean
        elif definition.answer_type == "scale":
            encoded = response.value_scale
        elif definition.answer_type == "single_choice":
            options = json.loads(definition.options) if definition.options else []
            encoded = (
                options.index(response.value_choice) if response.value_choice in options else None
            )
        else:
            encoded = None
        row_for(date.fromisoformat(instance.scheduled_local[:10])).checkins[definition.id] = encoded

    return [rows[day] for day in sorted(rows)]
