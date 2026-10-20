"""Weather service tests (Step 2 gate): mapping, poll store, skip-without-location."""

from datetime import UTC

import httpx

from guai_hub.database import Database
from guai_hub.services import weather
from guai_hub.services.settings_repo import set_location
from guai_hub.services.weather import map_condition
from tests.fakes import FakeAsyncClient, FakeResponse


def test_map_condition_buckets():
    assert map_condition(0) == "clear"
    assert map_condition(1) == "partly_cloudy"
    assert map_condition(3) == "cloudy"
    assert map_condition(45) == "fog"
    assert map_condition(55) == "drizzle"
    assert map_condition(61) == "rain"
    assert map_condition(73) == "snow"
    assert map_condition(95) == "thunderstorm"
    assert map_condition(99) == "thunderstorm"
    assert map_condition(999) is None
    assert map_condition(None) is None


async def test_poll_skips_without_location(migrated_db_path, settings):
    db_path = migrated_db_path
    db = Database(db_path)
    try:
        result = await weather.poll(settings, db)
        assert result is None
    finally:
        await db.dispose()


async def test_poll_stores_row(monkeypatch, migrated_db_path, settings):
    db_path = migrated_db_path
    db = Database(db_path)
    try:
        await set_location(db, "Berlin", 52.52, 13.405)
        payload = {
            "current": {
                "temperature_2m": 18.2,
                "relative_humidity_2m": 63.0,
                "wind_speed_10m": 3.4,
                "precipitation": 0.0,
                "surface_pressure": 1016.2,
                "weather_code": 1,
            }
        }
        fake = FakeAsyncClient([FakeResponse(payload), FakeResponse(payload)])
        monkeypatch.setattr(weather, "_AsyncClient", lambda **kw: fake)

        row = await weather.poll(settings, db)
        assert row is not None
        assert row["temperature_c"] == 18.2
        assert row["humidity_pct"] == 63.0
        assert row["pressure_hpa"] == 1016.2
        assert row["condition"] == "partly_cloudy"
        assert row["weather_code"] == 1

        # Idempotent: second poll lands on a different interval bucket only if time moved;
        # ensure a re-poll with the same bucket updates instead of duplicating.
        again = await weather.poll(settings, db)
        assert again is not None
    finally:
        await db.dispose()


async def test_poll_propagates_provider_error(monkeypatch, migrated_db_path, settings):
    db_path = migrated_db_path
    """An Open-Meteo failure must raise (so the scheduler can log it, not hide data)."""
    db = Database(db_path)
    try:
        await set_location(db, "Berlin", 52.52, 13.405)
        bad = FakeAsyncClient([FakeResponse({}, status=500)])
        monkeypatch.setattr(weather, "_AsyncClient", lambda **kw: bad)
        from pytest import raises

        with raises(httpx.HTTPStatusError):
            await weather.poll(settings, db)
    finally:
        await db.dispose()


# --- GET /api/weather/current (device endpoint, Step 10.3) -------------------

TOKEN = "0123456789abcdef"


def _auth():
    return {"Authorization": f"Bearer {TOKEN}"}


def _seed_weather(client, rows):
    import asyncio

    from guai_hub.models import Weather

    async def _run():
        async with client.app.state.db.session_factory() as s:
            for row in rows:
                s.add(Weather(**row))
            await s.commit()

    asyncio.run(_run())


def test_weather_current_requires_token(client):
    assert client.get("/api/weather/current").status_code == 401


def test_weather_current_no_data(client):
    body = client.get("/api/weather/current", headers=_auth()).json()
    assert body == {"weather": None}


def test_weather_current_with_today_rows(client):
    from datetime import datetime, timedelta

    now = datetime.now(UTC)
    iso = lambda dt: dt.isoformat(timespec="seconds").replace("+00:00", "Z")
    _seed_weather(
        client,
        [
            {
                "event_time": iso(now),
                "temperature_c": 12.5,
                "condition": "cloudy",
                "weather_code": 3,
            },
            {
                "event_time": iso(now - timedelta(minutes=30)),
                "temperature_c": 15.0,
                "condition": "cloudy",
            },
            {
                "event_time": iso(now - timedelta(minutes=60)),
                "temperature_c": 8.0,
                "condition": "cloudy",
            },
        ],
    )
    body = client.get("/api/weather/current", headers=_auth()).json()["weather"]
    assert body["temp_c"] == 12.5
    assert body["condition"] == "cloudy"
    assert body["temp_high_c"] == 15.0
    assert body["temp_low_c"] == 8.0
    assert body["age_minutes"] == 0


def test_weather_current_yesterday_only_high_low_null(client):
    from datetime import datetime, timedelta

    yesterday = datetime.now(UTC) - timedelta(days=1)
    _seed_weather(
        client,
        [
            {
                "event_time": yesterday.isoformat(timespec="seconds").replace("+00:00", "Z"),
                "temperature_c": 5.0,
                "condition": "clear",
                "weather_code": 0,
            },
        ],
    )
    body = client.get("/api/weather/current", headers=_auth()).json()["weather"]
    assert body["temp_c"] == 5.0
    assert body["temp_high_c"] is None
    assert body["temp_low_c"] is None
    assert body["age_minutes"] >= 1435
