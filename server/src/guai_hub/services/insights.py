"""Insight endpoints: /api/daily, /api/stats, correlations, and CSV export.

Everything is aggregated in Python from ``TZ`` day boundaries (local date string
keys). See spec/data-and-api.md for the response contracts.
"""

from __future__ import annotations

import csv
import io
import json
from collections import Counter
from datetime import UTC, datetime, timedelta
from datetime import date as date_cls
from datetime import time as dtime

from fastapi import HTTPException
from sqlalchemy import select

from guai_hub.config import Settings
from guai_hub.database import Database
from guai_hub.models import (
    Activity,
    CheckinDefinition,
    CheckinInstance,
    CheckinResponse,
    HeartRate,
    Sleep,
    Weather,
)
from guai_hub.services.checkins import from_utc_iso

WEATHER_METRICS = ("temperature_c", "humidity_pct", "pressure_hpa", "precipitation_mm")
WEATHER_AGG = {
    "temperature_c": "avg",
    "humidity_pct": "avg",
    "pressure_hpa": "avg",
    "precipitation_mm": "sum",
}


# --- local-day helpers -------------------------------------------------------


def local_day_start(day: date_cls, tz) -> datetime:
    return datetime.combine(day, dtime.min, tzinfo=tz).astimezone(UTC)


def local_day_end(day: date_cls, tz) -> datetime:
    return datetime.combine(day + timedelta(days=1), dtime.min, tzinfo=tz).astimezone(UTC)


def local_date_of(iso_utc: str, tz) -> str:
    return from_utc_iso(iso_utc).astimezone(tz).date().isoformat()


def _iso(dt: datetime) -> str:
    return dt.isoformat().replace("+00:00", "Z")


# --- fetch helpers -----------------------------------------------------------


async def _fetch(
    db, model, *, start_utc: datetime | None = None, end_utc: datetime | None = None, order_by=None
):
    stmt = select(model)
    if start_utc is not None:
        stmt = stmt.where(model.event_time >= _iso(start_utc))
    if end_utc is not None:
        stmt = stmt.where(model.event_time < _iso(end_utc))
    if order_by is not None:
        stmt = stmt.order_by(order_by)
    async with db.session_factory() as session:
        return list((await session.execute(stmt)).scalars().all())


async def _all_rows(db, model, order_by):
    async with db.session_factory() as session:
        return list((await session.execute(select(model).order_by(order_by))).scalars().all())


async def _sleep_rows(db, from_local: str | None, to_local: str | None) -> list[Sleep]:
    stmt = select(Sleep)
    if from_local:
        stmt = stmt.where(Sleep.night_date >= from_local)
    if to_local:
        stmt = stmt.where(Sleep.night_date <= to_local)
    async with db.session_factory() as session:
        return list((await session.execute(stmt)).scalars().all())


# --- /api/daily --------------------------------------------------------------


async def daily_rows(
    settings: Settings, db: Database, from_local: str | None = None, to_local: str | None = None
) -> list[dict]:
    """One row per local date, bucketed in ``TZ``."""
    tz = settings.timezone
    if from_local:
        start_utc = local_day_start(date_cls.fromisoformat(from_local), tz)
    else:
        start_utc = None
    end_utc = local_day_end(date_cls.fromisoformat(to_local), tz) if to_local else None

    activity = await _fetch(db, Activity, start_utc=start_utc, end_utc=end_utc)
    heart = await _fetch(db, HeartRate, start_utc=start_utc, end_utc=end_utc)
    weather = await _fetch(db, Weather, start_utc=start_utc, end_utc=end_utc)
    sleep = await _sleep_rows(db, from_local, to_local)

    async with db.session_factory() as session:
        inst_rows = (
            await session.execute(
                select(CheckinInstance, CheckinDefinition).join(
                    CheckinDefinition, CheckinInstance.definition_id == CheckinDefinition.id
                )
            )
        ).all()
        resp_rows = (
            await session.execute(
                select(CheckinResponse, CheckinInstance).join(
                    CheckinInstance, CheckinResponse.instance_id == CheckinInstance.id
                )
            )
        ).all()
        defn_by_id = {
            d.id: d for d in (await session.execute(select(CheckinDefinition))).scalars().all()
        }

    dates: dict[str, dict] = {}
    for row in activity:
        d = dates.setdefault(local_date_of(row.event_time, tz), {})
        acts = d.setdefault("activity", {})
        _max_inplace(acts, row)
    for row in sleep:
        dates.setdefault(row.night_date, {})["sleep"] = row
    for row in heart:
        d = dates.setdefault(local_date_of(row.event_time, tz), {}).setdefault("hr", [])
        d.append(row.bpm)
    for row in weather:
        dates.setdefault(local_date_of(row.event_time, tz), {}).setdefault("weather", []).append(
            row
        )
    for inst, defn in inst_rows:
        if inst.status == "missed":
            slot = dates.setdefault(inst.scheduled_local[:10], {}).setdefault("checkins", {})
            summary = slot.setdefault(defn.id, {"answered": 0, "missed": 0, "values": []})
            summary["missed"] += 1
    for resp, inst in resp_rows:
        slot = dates.setdefault(inst.scheduled_local[:10], {}).setdefault("checkins", {})
        summary = slot.setdefault(inst.definition_id, {"answered": 0, "missed": 0, "values": []})
        summary["answered"] += 1
        defn = defn_by_id[inst.definition_id]
        summary["values"].append((defn.answer_type, _resp_value(resp, defn.answer_type)))

    return [_compose(day, dates[day]) for day in sorted(dates)]


def _max_inplace(acts: dict, row) -> None:
    for key in ("steps", "distance_meters", "active_seconds", "resting_kcal", "active_kcal"):
        value = getattr(row, key)
        if value is None:
            continue
        if key not in acts or acts[key] is None:
            acts[key] = value
        else:
            acts[key] = max(acts[key], value)


def _resp_value(resp: CheckinResponse, answer_type: str):
    if answer_type == "boolean":
        return bool(resp.value_boolean)
    if answer_type == "scale":
        return resp.value_scale
    return resp.value_choice


def _compose(day: str, d: dict) -> dict:
    act = d.get("activity") or {}
    sleep = d.get("sleep")
    hr = d.get("hr") or []
    wx = d.get("weather") or []

    checkin_out = {}
    for def_id, summary in (d.get("checkins") or {}).items():
        values = summary["values"]
        entry = {"answered": summary["answered"], "missed": summary["missed"]}
        by_type: dict[str, list] = {}
        for atype, value in values:
            by_type.setdefault(atype, []).append(value)
        for atype, vals in by_type.items():
            if atype == "boolean":
                entry["value_boolean_true_pct"] = round(
                    100 * sum(1 for v in vals if v) / len(vals), 1
                )
            elif atype == "scale":
                entry["value_scale_avg"] = round(sum(vals) / len(vals), 2)
            else:
                entry["value_choice_top"] = Counter(vals).most_common(1)[0][0]
        checkin_out[def_id] = entry

    restful_pct = None
    if sleep and sleep.sleep_seconds:
        restful_pct = round(100 * (sleep.restful_seconds or 0) / sleep.sleep_seconds, 1)

    temps = [w.temperature_c for w in wx if w.temperature_c is not None]
    return {
        "local_date": day,
        "steps": act.get("steps"),
        "distance_meters": act.get("distance_meters"),
        "active_seconds": act.get("active_seconds"),
        "resting_kcal": act.get("resting_kcal"),
        "active_kcal": act.get("active_kcal"),
        "sleep_seconds": sleep.sleep_seconds if sleep else None,
        "restful_seconds": sleep.restful_seconds if sleep else None,
        "restful_pct": restful_pct,
        "hr_avg_bpm": _avg(hr),
        "hr_min_bpm": min(hr) if hr else None,
        "hr_max_bpm": max(hr) if hr else None,
        "temp_min_c": min(temps) if temps else None,
        "temp_max_c": max(temps) if temps else None,
        "temp_avg_c": _avg(temps),
        "humidity_avg_pct": _avg([w.humidity_pct for w in wx]),
        "pressure_avg_hpa": _avg([w.pressure_hpa for w in wx]),
        "precip_total_mm": _sum([w.precipitation_mm for w in wx]),
        "checkins": checkin_out,
    }


def _avg(values):
    vals = [v for v in values if v is not None]
    return round(sum(vals) / len(vals), 2) if vals else None


def _sum(values):
    vals = [v for v in values if v is not None]
    return round(sum(vals), 2) if vals else None


# --- /api/stats --------------------------------------------------------------


async def stats(settings: Settings, db: Database) -> dict:
    tz = settings.timezone
    now = datetime.now(UTC)
    today = now.astimezone(tz).date()

    activity_today = await _fetch(
        db, Activity, start_utc=local_day_start(today, tz), end_utc=local_day_end(today, tz)
    )
    today_steps = activity_today[-1].steps if activity_today else None

    sleep = await _sleep_rows(db, None, None)
    today_sleep = sleep[-1].sleep_seconds if sleep else None

    days = [today - timedelta(days=i) for i in range(6, -1, -1)]
    day_steps: list[int] = []
    day_sleep: list[int] = []
    for day in days:
        rows = await _fetch(
            db, Activity, start_utc=local_day_start(day, tz), end_utc=local_day_end(day, tz)
        )
        max_steps = max((r.steps for r in rows if r.steps is not None), default=None)
        if max_steps is not None:
            day_steps.append(max_steps)
        night = [s for s in sleep if s.night_date == day.isoformat()]
        if night and night[0].sleep_seconds is not None:
            day_sleep.append(night[0].sleep_seconds)

    hr_rows = await _fetch(
        db, HeartRate, start_utc=local_day_start(days[0], tz), end_utc=local_day_end(today, tz)
    )
    hr_avg = _avg([r.bpm for r in hr_rows])

    async with db.session_factory() as session:
        all_inst = (await session.execute(select(CheckinInstance))).scalars().all()

    pending = sum(1 for i in all_inst if i.status == "pending")
    missed_24h = sum(
        1 for i in all_inst if i.missed_at and now - from_utc_iso(i.missed_at) <= timedelta(days=1)
    )
    answered_7d = sum(
        1
        for i in all_inst
        if i.answered_at and now - from_utc_iso(i.answered_at) <= timedelta(days=7)
    )
    missed_7d = sum(
        1 for i in all_inst if i.missed_at and now - from_utc_iso(i.missed_at) <= timedelta(days=7)
    )

    return {
        "today_steps": today_steps,
        "today_sleep_seconds": today_sleep,
        "7d_avg_steps": round(sum(day_steps) / len(day_steps), 1) if day_steps else None,
        "7d_avg_sleep_seconds": round(sum(day_sleep) / len(day_sleep), 1) if day_sleep else None,
        "7d_avg_hr": hr_avg,
        "pending_checkins": pending,
        "missed_24h": missed_24h,
        "answered_7d": answered_7d,
        "missed_7d": missed_7d,
    }


# --- correlations ------------------------------------------------------------


async def sleep_vs_checkin(db: Database, definition_id: str) -> list[dict]:
    async with db.session_factory() as session:
        defn = await session.get(CheckinDefinition, definition_id)
        if defn is None:
            raise HTTPException(status_code=404, detail="check-in not found")
        if defn.answer_type != "scale":
            raise HTTPException(
                status_code=422, detail="sleep correlation requires a scale check-in"
            )
        rows = (
            await session.execute(
                select(CheckinResponse, CheckinInstance)
                .join(CheckinInstance, CheckinResponse.instance_id == CheckinInstance.id)
                .where(CheckinInstance.definition_id == definition_id)
                .order_by(CheckinInstance.scheduled_utc)
            )
        ).all()
        nights = {s.night_date: s for s in (await session.execute(select(Sleep))).scalars().all()}

    out = []
    for resp, inst in rows:
        day = date_cls.fromisoformat(inst.scheduled_local[:10])
        night = nights.get((day - timedelta(days=1)).isoformat())
        if night and night.sleep_seconds is not None:
            out.append(
                {
                    "date": day.isoformat(),
                    "sleep_seconds": night.sleep_seconds,
                    "value": resp.value_scale,
                }
            )
    return out


async def weather_vs_checkin(
    settings: Settings, db: Database, definition_id: str, metric: str
) -> list[dict]:
    """Weather on the same *local* day as the check-in answer."""
    if metric not in WEATHER_METRICS:
        raise HTTPException(
            status_code=422, detail=f"metric must be one of {list(WEATHER_METRICS)}"
        )
    tz = settings.timezone
    async with db.session_factory() as session:
        defn = await session.get(CheckinDefinition, definition_id)
        if defn is None:
            raise HTTPException(status_code=404, detail="check-in not found")
        if defn.answer_type not in ("scale", "boolean"):
            raise HTTPException(
                status_code=422, detail="weather correlation requires a scale or boolean check-in"
            )
        rows = (
            await session.execute(
                select(CheckinResponse, CheckinInstance)
                .join(CheckinInstance, CheckinResponse.instance_id == CheckinInstance.id)
                .where(CheckinInstance.definition_id == definition_id)
                .order_by(CheckinInstance.scheduled_utc)
            )
        ).all()
        weather = (await session.execute(select(Weather))).scalars().all()

    by_day: dict[str, list] = {}
    for row in weather:
        value = getattr(row, metric)
        if value is not None:
            by_day.setdefault(local_date_of(row.event_time, tz), []).append(value)

    agg = WEATHER_AGG[metric]
    out = []
    for resp, inst in rows:
        day = inst.scheduled_local[:10]
        vals = by_day.get(day)
        if not vals:
            continue
        metric_value = (sum(vals) / len(vals)) if agg == "avg" else sum(vals)
        value = bool(resp.value_boolean) if defn.answer_type == "boolean" else resp.value_scale
        out.append({"date": day, "metric": round(metric_value, 2), "value": value})
    return out


# --- CSV export --------------------------------------------------------------

EXPORT_COLUMNS = {
    "daily": [
        "local_date",
        "steps",
        "distance_meters",
        "active_seconds",
        "resting_kcal",
        "active_kcal",
        "sleep_seconds",
        "restful_seconds",
        "restful_pct",
        "hr_avg_bpm",
        "hr_min_bpm",
        "hr_max_bpm",
        "temp_min_c",
        "temp_max_c",
        "temp_avg_c",
        "humidity_avg_pct",
        "pressure_avg_hpa",
        "precip_total_mm",
        "checkins",
    ],
    "activity": [
        "event_time",
        "steps",
        "distance_meters",
        "resting_kcal",
        "active_kcal",
        "active_seconds",
    ],
    "sleep": ["night_date", "sleep_seconds", "restful_seconds"],
    "heart_rate": ["event_time", "bpm", "bpm_raw"],
    "weather": [
        "event_time",
        "temperature_c",
        "humidity_pct",
        "wind_speed_mps",
        "precipitation_mm",
        "pressure_hpa",
        "weather_code",
        "condition",
    ],
    "checkins": ["id", "title", "answer_type", "value", "scheduled_local", "submitted_at"],
}


async def export_csv(settings: Settings, db: Database, kind: str) -> tuple[str, str, str]:
    """Return (filename, media_type, csv_text)."""
    if kind not in EXPORT_COLUMNS:
        raise HTTPException(status_code=422, detail=f"kind must be one of {list(EXPORT_COLUMNS)}")
    columns = EXPORT_COLUMNS[kind]

    if kind == "daily":
        rows = [
            {
                col: (
                    json.dumps(r.get(col), ensure_ascii=False) if col == "checkins" else r.get(col)
                )
                for col in columns
            }
            for r in await daily_rows(settings, db)
        ]
    elif kind == "checkins":
        async with db.session_factory() as session:
            data = (
                await session.execute(
                    select(CheckinResponse, CheckinInstance, CheckinDefinition)
                    .join(CheckinInstance, CheckinResponse.instance_id == CheckinInstance.id)
                    .join(CheckinDefinition, CheckinInstance.definition_id == CheckinDefinition.id)
                    .order_by(CheckinResponse.submitted_at)
                )
            ).all()
        rows = []
        for resp, inst, defn in data:
            rows.append(
                {
                    "id": resp.id,
                    "title": defn.title,
                    "answer_type": defn.answer_type,
                    "value": json.dumps(_resp_value(resp, defn.answer_type), ensure_ascii=False),
                    "scheduled_local": inst.scheduled_local,
                    "submitted_at": resp.submitted_at,
                }
            )
    else:
        model = {"activity": Activity, "sleep": Sleep, "heart_rate": HeartRate, "weather": Weather}[
            kind
        ]
        objs = await _all_rows(
            db, model, order_by="event_time" if kind != "sleep" else "night_date"
        )
        rows = [{col: getattr(o, col, None) for col in columns} for o in objs]

    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=columns)
    writer.writeheader()
    for r in rows:
        writer.writerow({k: "" if v is None else v for k, v in r.items()})
    return (f"guai-{kind}.csv", "text/csv", buf.getvalue())
