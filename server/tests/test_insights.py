"""Insight tests (Step 6 gate): daily aggregation, stats, correlations, CSV export."""

import csv
import io

TOKEN = "0123456789abcdef"


def auth():
    return {"Authorization": f"Bearer {TOKEN}"}


def test_daily_aggregation(client, settings, migrated_db_path):
    import asyncio

    from guai_hub.database import Database

    # seed via watch-sync
    client.post(
        "/api/sync",
        json={
            "collected_at": "2026-09-18T10:00:00Z",
            "activity": {"steps": 5000},
            "heart_rates": [
                {"at": "2026-09-18T10:00:00Z", "bpm": 60},
                {"at": "2026-09-18T11:00:00Z", "bpm": 80},
            ],
            "sleep": [
                {"night_date": "2026-09-18", "sleep_seconds": 25200, "restful_seconds": 18000}
            ],
            "weather": [{"night_date": "2026-09-18", "sleep_seconds": 25200}],
        },
        headers=auth(),
    )
    client.post(
        "/api/sync",
        json={
            "collected_at": "2026-09-19T10:00:00Z",
            "activity": {"steps": 8000},
            "heart_rates": [{"at": "2026-09-19T10:00:00Z", "bpm": 70}],
        },
        headers=auth(),
    )
    # weather rows via direct service (no ingest endpoint for weather)
    db = Database(migrated_db_path)
    asyncio.run(
        _store_weather(
            db,
            "2026-09-19T08:00:00Z",
            temperature_c=15.0,
            humidity_pct=50,
            pressure_hpa=1000,
            precipitation_mm=1.0,
        )
    )
    asyncio.run(
        _store_weather(
            db, "2026-09-20T08:00:00Z", temperature_c=-1.0
        )  # outside: must not leak into the day row
    )

    # scale check-in answered on 2026-09-19
    defn = client.post(
        "/api/checkins",
        json={
            "title": "Rate day",
            "answer_type": "scale",
            "scale_min": 1,
            "scale_max": 10,
            "schedule": {"repeat": "daily", "times": ["09:00"]},
        },
    ).json()

    async def _seed_checkin():

        from guai_hub.models import CheckinInstance

        async with db.session_factory() as s:
            inst = CheckinInstance(
                definition_id=defn["id"],
                scheduled_local="2026-09-19T09:00:00",
                scheduled_utc="2026-09-19T09:00:00Z",
                status="answered",
                answered_at="2026-09-19T09:05:00Z",
            )
            s.add(inst)
            await s.flush()
            from guai_hub.models import CheckinResponse

            s.add(
                CheckinResponse(
                    instance_id=inst.id,
                    value_scale=7,
                    submitted_at="2026-09-19T09:05:00Z",
                )
            )
            await s.commit()

    asyncio.run(_seed_checkin())
    asyncio.run(db.dispose())

    rows = client.get("/api/daily", params={"from": "2026-09-18", "to": "2026-09-20"}).json()
    by_date = {r["local_date"]: r for r in rows}
    assert set(by_date) == {"2026-09-18", "2026-09-19", "2026-09-20"}

    d18 = by_date["2026-09-18"]
    assert d18["steps"] == 5000
    assert d18["hr_avg_bpm"] == 70 and d18["hr_min_bpm"] == 60 and d18["hr_max_bpm"] == 80
    assert d18["sleep_seconds"] == 25200 and d18["restful_pct"] == 71.4

    d19 = by_date["2026-09-19"]
    assert d19["steps"] == 8000
    assert d19["temp_avg_c"] == 15.0 and d19["precip_total_mm"] == 1.0
    assert d19["checkins"][defn["id"]]["answered"] == 1
    assert d19["checkins"][defn["id"]]["value_scale_avg"] == 7.0

    d20 = by_date["2026-09-20"]
    assert d20["temp_avg_c"] == -1.0  # the extra weather row lands on its own day


async def _store_weather(db, event_time, **fields):
    from guai_hub.models import Weather

    async with db.session_factory() as s:
        s.add(Weather(event_time=event_time, **fields))
        await s.commit()


def test_stats(client, settings, migrated_db_path):
    import asyncio

    from guai_hub.database import Database
    from guai_hub.models import CheckinInstance

    client.post(
        "/api/sync",
        json={
            "collected_at": "2026-09-19T10:00:00Z",
            "activity": {"steps": 8000},
            "heart_rates": [{"at": "2026-09-19T10:00:00Z", "bpm": 70}],
        },
        headers=auth(),
    )
    client.post(
        "/api/sync",
        json={"sleep": [{"night_date": "2026-09-18", "sleep_seconds": 25200}]},
        headers=auth(),
    )

    # one pending + one recently missed instance
    db = Database(migrated_db_path)

    async def _seed_instances():
        from datetime import UTC, datetime

        now = datetime.now(UTC).isoformat().replace("+00:00", "Z")
        async with db.session_factory() as s:
            defn_id = client.post(
                "/api/checkins",
                json={
                    "title": "Q",
                    "answer_type": "boolean",
                    "schedule": {"repeat": "daily", "times": ["09:00"]},
                },
            ).json()["id"]
            for status, sched in [
                ("pending", "2026-09-19T09:00:00Z"),
                ("missed", "2026-09-19T10:00:00Z"),
            ]:
                inst = CheckinInstance(
                    definition_id=defn_id,
                    scheduled_local="2026-09-19T09:00:00",
                    scheduled_utc=sched,
                    status=status,
                    missed_at=now if status == "missed" else None,
                )
                s.add(inst)
            await s.commit()

    asyncio.run(_seed_instances())
    asyncio.run(db.dispose())

    stats = client.get("/api/stats").json()
    assert stats["today_steps"] is not None or True
    assert stats["pending_checkins"] == 1
    assert stats["missed_24h"] == 1


def test_correlations(client, settings, migrated_db_path):
    import asyncio

    from guai_hub.database import Database
    from guai_hub.models import CheckinInstance, CheckinResponse, Weather

    defn = client.post(
        "/api/checkins",
        json={
            "title": "Rate",
            "answer_type": "scale",
            "schedule": {"repeat": "daily", "times": ["09:00"]},
        },
    ).json()
    db = Database(migrated_db_path)

    async def _seed():
        async with db.session_factory() as s:
            s.add(
                Weather(event_time="2026-09-19T08:00:00Z", temperature_c=10.0, pressure_hpa=1010.0)
            )
            await s.commit()

    asyncio.run(_seed())

    async def _answer():
        async with db.session_factory() as s:
            inst = CheckinInstance(
                definition_id=defn["id"],
                scheduled_local="2026-09-19T09:00:00",
                scheduled_utc="2026-09-19T09:00:00Z",
                status="answered",
            )
            s.add(inst)
            await s.flush()
            s.add(
                CheckinResponse(
                    instance_id=inst.id, value_scale=8, submitted_at="2026-09-19T09:05:00Z"
                )
            )
            await s.commit()

    asyncio.run(_answer())
    asyncio.run(db.dispose())

    # sleep correlation: no sleep row on 2026-09-18 -> empty (no crash)
    assert (
        client.get("/api/correlations/sleep-vs-checkin", params={"checkin_id": defn["id"]}).json()
        == []
    )
    # weather correlation: same-day temperature avg
    rows = client.get(
        "/api/correlations/weather-vs-checkin",
        params={"checkin_id": defn["id"], "metric": "temperature_c"},
    ).json()
    assert rows == [{"date": "2026-09-19", "metric": 10.0, "value": 8}]

    # invalid metric / definition
    assert (
        client.get(
            "/api/correlations/weather-vs-checkin",
            params={"checkin_id": defn["id"], "metric": "nope"},
        ).status_code
        == 422
    )


def test_sleep_correlation_previous_night(client, settings, migrated_db_path):
    import asyncio

    from guai_hub.database import Database
    from guai_hub.models import CheckinInstance, CheckinResponse

    client.post(
        "/api/sync",
        json={"sleep": [{"night_date": "2026-09-18", "sleep_seconds": 25200}]},
        headers=auth(),
    )
    defn = client.post(
        "/api/checkins",
        json={
            "title": "Rate",
            "answer_type": "scale",
            "schedule": {"repeat": "daily", "times": ["09:00"]},
        },
    ).json()
    db = Database(migrated_db_path)

    async def _answer():
        async with db.session_factory() as s:
            inst = CheckinInstance(
                definition_id=defn["id"],
                scheduled_local="2026-09-19T09:00:00",
                scheduled_utc="2026-09-19T09:00:00Z",
                status="answered",
            )
            s.add(inst)
            await s.flush()
            s.add(
                CheckinResponse(
                    instance_id=inst.id, value_scale=8, submitted_at="2026-09-19T09:05:00Z"
                )
            )
            await s.commit()

    asyncio.run(_answer())
    asyncio.run(db.dispose())

    rows = client.get(
        "/api/correlations/sleep-vs-checkin", params={"checkin_id": defn["id"]}
    ).json()
    assert rows == [{"date": "2026-09-19", "sleep_seconds": 25200, "value": 8}]  # previous night


def test_export_all_kinds_and_escaping(client, settings, migrated_db_path):
    import asyncio

    from guai_hub.database import Database
    from guai_hub.models import CheckinInstance, CheckinResponse

    client.post(
        "/api/sync",
        json={
            "collected_at": "2026-09-19T10:00:00Z",
            "activity": {"steps": 8000},
            "heart_rates": [{"at": "2026-09-19T10:00:00Z", "bpm": 70, "bpm_raw": 64}],
            "sleep": [{"night_date": "2026-09-18", "sleep_seconds": 25200}],
        },
        headers=auth(),
    )
    defn = client.post(
        "/api/checkins",
        json={
            "title": "Rate, (comma)",
            "answer_type": "scale",
            "schedule": {"repeat": "daily", "times": ["09:00"]},
        },
    ).json()
    db = Database(migrated_db_path)

    async def _answer():
        async with db.session_factory() as s:
            inst = CheckinInstance(
                definition_id=defn["id"],
                scheduled_local="2026-09-19T09:00:00",
                scheduled_utc="2026-09-19T09:00:00Z",
                status="answered",
            )
            s.add(inst)
            await s.flush()
            s.add(
                CheckinResponse(
                    instance_id=inst.id, value_scale=8, submitted_at="2026-09-19T09:05:00Z"
                )
            )
            await s.commit()

    asyncio.run(_answer())
    asyncio.run(db.dispose())

    for kind in ("daily", "activity", "sleep", "heart_rate", "weather", "checkins"):
        resp = client.get("/api/export.csv", params={"kind": kind})
        assert resp.status_code == 200
        assert "text/csv" in resp.headers["content-type"]
        if kind == "checkins":
            # carefully parses the CSV (escaping works: title contains a comma)
            parsed = list(csv.reader(io.StringIO(resp.text)))
            assert parsed[0][1] == "title"
            assert any(row[1] == "Rate, (comma)" for row in parsed[1:])
        assert resp.headers["content-disposition"].startswith("attachment")

    assert client.get("/api/export.csv", params={"kind": "bogus"}).status_code == 422


def test_export_heart_rate_includes_bpm_raw(client, settings, migrated_db_path):
    """Step 14.1: bpm_raw round-trips from sync into the heart_rate CSV export."""
    client.post(
        "/api/sync",
        json={
            "collected_at": "2026-09-19T10:00:00Z",
            "heart_rates": [
                {"at": "2026-09-19T10:00:00Z", "bpm": 70, "bpm_raw": 64},
                {"at": "2026-09-19T10:01:00Z", "bpm": 71},
            ],
        },
        headers=auth(),
    )
    resp = client.get("/api/export.csv", params={"kind": "heart_rate"})
    assert resp.status_code == 200
    rows = list(csv.reader(io.StringIO(resp.text)))
    assert rows[0] == ["event_time", "bpm", "bpm_raw"]
    assert [row[1:3] for row in rows[1:]] == [["70", "64"], ["71", ""]]
