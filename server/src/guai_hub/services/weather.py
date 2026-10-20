"""Weather service: Open-Meteo polling, WMO -> condition mapping, interval store."""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from datetime import time as dtime

import httpx
from sqlalchemy import select

from guai_hub.config import Settings
from guai_hub.database import Database
from guai_hub.models import Weather
from guai_hub.services.settings_repo import get_location
from guai_hub.timeutil import floor_interval_utc

logger = logging.getLogger(__name__)

OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"
_AsyncClient = httpx.AsyncClient
FIELDS = (
    "temperature_2m,relative_humidity_2m,wind_speed_10m,precipitation,surface_pressure,weather_code"
)

# WMO weather interpretation codes -> coarse condition buckets (spec/data-and-api.md).
WMO: dict[tuple[int, int], str] = {
    (0, 0): "clear",
    (1, 2): "partly_cloudy",
    (3, 3): "cloudy",
    (45, 48): "fog",
    (51, 57): "drizzle",
    (61, 67): "rain",
    (71, 77): "snow",
    (80, 82): "rain",
    (85, 86): "snow",
    (95, 99): "thunderstorm",
}


def map_condition(code: int | None) -> str | None:
    if code is None:
        return None
    for (lo, hi), condition in WMO.items():
        if lo <= code <= hi:
            return condition
    return None


async def fetch_current(latitude: float, longitude: float, timeout: float = 15.0) -> dict:
    """Fetch Open-Meteo current conditions. Raises on HTTP/provider errors."""
    client = _AsyncClient(timeout=timeout)
    try:
        resp = await client.get(
            OPEN_METEO_URL,
            params={
                "latitude": latitude,
                "longitude": longitude,
                "current": FIELDS,
                "wind_speed_unit": "ms",
            },
        )
        resp.raise_for_status()
    finally:
        await client.aclose()
    return resp.json().get("current", {})


async def poll(settings: Settings, db: Database) -> dict | None:
    """Fetch and store one weather row at the interval grid. Returns the row dict or ``None``
    if no location is configured (logged, not an error)."""
    location = await get_location(db)
    if location is None:
        logger.info("weather: no location configured; skipping poll")
        return None

    data = await fetch_current(location["latitude"], location["longitude"])

    code = data.get("weather_code")
    event_time = floor_interval_utc(datetime.now(UTC), settings.weather_interval_minutes)
    row = Weather(
        event_time=event_time,
        temperature_c=_num(data.get("temperature_2m")),
        humidity_pct=_num(data.get("relative_humidity_2m")),
        wind_speed_mps=_num(data.get("wind_speed_10m")),
        precipitation_mm=_num(data.get("precipitation")),
        pressure_hpa=_num(data.get("surface_pressure")),
        weather_code=code,
        condition=map_condition(code),
    )
    async with db.session_factory() as session:
        await session.merge(row)
        await session.commit()
    return row.model_dump()


def _num(value) -> float | None:
    if value is None:
        return None
    return float(value)


def _iso(dt: datetime) -> str:
    return dt.isoformat().replace("+00:00", "Z")


async def current(settings: Settings, db: Database) -> dict | None:
    """Latest conditions + today's high/low for the watchface (device endpoint).

    Returns ``None`` when nothing has been polled yet. ``temp_high_c``/``temp_low_c``
    are ``None`` when there are no weather rows for today (local date in ``TZ``).
    """
    tz = settings.timezone
    async with db.session_factory() as session:
        latest = (
            await session.execute(select(Weather).order_by(Weather.event_time.desc()).limit(1))
        ).scalar_one_or_none()
        if latest is None:
            return None

        now = datetime.now(UTC)
        latest_dt = datetime.fromisoformat(latest.event_time)
        age_minutes = max(0, int((now - latest_dt).total_seconds() // 60))

        today = now.astimezone(tz).date()
        day_start = datetime.combine(today, dtime.min, tzinfo=tz).astimezone(UTC)
        rows = (
            await session.execute(
                select(Weather).where(
                    Weather.event_time >= _iso(day_start),
                    Weather.event_time < _iso(day_start + timedelta(days=1)),
                )
            )
        ).scalars().all()

    temps = [r.temperature_c for r in rows if r.temperature_c is not None]
    return {
        "temp_c": latest.temperature_c,
        "condition": latest.condition,
        "temp_high_c": max(temps) if temps else None,
        "temp_low_c": min(temps) if temps else None,
        "age_minutes": age_minutes,
    }
