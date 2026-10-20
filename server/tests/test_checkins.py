"""Check-in engine tests (Step 3 gate): schedule validation, CRUD, TZ-aware
tick (materialisation/missed), and the device answer endpoints."""

from datetime import UTC, datetime

import pytest

from guai_hub.database import Database
from guai_hub.schemas import Schedule
from guai_hub.services import checkins

TOKEN = "0123456789abcdef"


def auth() -> dict:
    return {"Authorization": f"Bearer {TOKEN}"}


def _def_payload(times=("09:00",), answer_type="boolean", schedule=None, **overrides):
    payload = {
        "title": "How are you feeling today?",
        "answer_type": answer_type,
        "schedule": schedule or {"repeat": "daily", "times": list(times)},
        "due_window_minutes": 60,
        "reminder_minutes": 0,
    }
    payload.update(overrides)
    return payload


# --- Schedule validation ---------------------------------------------------


def test_schedule_daily_normalises_times():
    s = Schedule.model_validate({"repeat": "daily", "times": ["21:05", "09:00"]})
    assert s.times == ["09:00", "21:05"]
    assert s.days is None and s.scheduled_at is None


def test_schedule_weekly_requires_days():
    with pytest.raises(ValueError):
        Schedule.model_validate({"repeat": "weekly", "times": ["08:30"]})


def test_schedule_weekly_valid_day_subset():
    s = Schedule.model_validate({"repeat": "weekly", "days": [5, 7, 1], "times": ["08:30"]})
    assert s.days == [1, 5, 7]


def test_schedule_once_requires_scheduled_at():
    with pytest.raises(ValueError):
        Schedule.model_validate({"repeat": "once"})
    s = Schedule.model_validate({"repeat": "once", "scheduled_at": "2026-10-01T09:00"})
    assert s.scheduled_at == "2026-10-01T09:00"


def test_schedule_rejects_bad_time():
    with pytest.raises(ValueError):
        Schedule.model_validate({"repeat": "daily", "times": ["25:00"]})


def test_schedule_rejects_unknown_fields():
    with pytest.raises(ValueError):
        Schedule.model_validate({"repeat": "daily", "times": ["09:00"], "bogus": 1})


# --- CRUD (HTTP, trusted LAN) ----------------------------------------------


def test_crud_roundtrip(client):
    # LAN endpoints are not token-protected
    created = client.post("/api/checkins", json=_def_payload(times=["09:00", "21:00"]))
    assert created.status_code == 201
    body = created.json()
    id_ = body["id"]
    assert body["active"] is True
    assert body["instance_counts"] == {"pending": 0, "answered": 0, "missed": 0}

    listed = client.get("/api/checkins").json()
    assert any(d["id"] == id_ for d in listed)

    assert client.get(f"/api/checkins/{id_}").json()["title"].startswith("How are")
    assert client.get(f"/api/checkins/{id_}").json()["schedule"]["times"] == ["09:00", "21:00"]

    updated = client.put(
        f"/api/checkins/{id_}", json=_def_payload(times=["10:00"], title="Updated?")
    )
    assert updated.status_code == 200
    assert updated.json()["title"] == "Updated?"

    # deactivate (soft delete)
    deactivated = client.post(f"/api/checkins/{id_}/deactivate")
    assert deactivated.status_code == 200
    assert deactivated.json()["active"] is False

    # delete is allowed with zero instances
    assert client.delete(f"/api/checkins/{id_}").status_code == 204
    assert client.get(f"/api/checkins/{id_}").status_code == 404


def test_crud_validation_errors(client):
    # bad answer type
    bad = _def_payload()
    bad["answer_type"] = "free_text"
    assert client.post("/api/checkins", json=bad).status_code == 422
    # single_choice without options
    sc = _def_payload(answer_type="single_choice")
    assert client.post("/api/checkins", json=sc).status_code == 422
    # scale with min >= max
    sc = _def_payload(answer_type="scale", scale_min=10, scale_max=10)
    assert client.post("/api/checkins", json=sc).status_code == 422
    # bad schedule
    bad_s = _def_payload()
    bad_s["schedule"] = {"repeat": "weekly", "times": ["08:30"]}  # missing days
    assert client.post("/api/checkins", json=bad_s).status_code == 422
    # unknown field
    unk = _def_payload()
    unk["nope"] = 1
    assert client.post("/api/checkins", json=unk).status_code == 422


def test_delete_definition_with_instances_is_409(client):
    created = client.post("/api/checkins", json=_def_payload()).json()
    id_ = created["id"]

    import asyncio

    asyncio.run(_insert_instance(client.app.state.settings.db_path, id_))
    assert client.delete(f"/api/checkins/{id_}").status_code == 409
    # deactivating is still allowed
    assert client.post(f"/api/checkins/{id_}/deactivate").status_code == 200


async def _insert_instance(db_path, definition_id):
    db = Database(db_path)
    async with db.session_factory() as s:
        from guai_hub.models import CheckinInstance

        s.add(
            CheckinInstance(
                definition_id=definition_id,
                scheduled_local="2026-10-19T09:00:00",
                scheduled_utc="2026-10-19T09:00:00Z",
                status="pending",
            )
        )
        await s.commit()
    await db.dispose()


# --- TZ-aware tick ---------------------------------------------------------


async def _counts(db, status=None):
    from sqlalchemy import func, select

    from guai_hub.models import CheckinInstance

    async with db.session_factory() as s:
        stmt = select(func.count()).select_from(CheckinInstance)
        if status:
            stmt = stmt.where(CheckinInstance.status == status)
        return (await s.execute(stmt)).scalar_one()


async def test_tick_materialises_daily_and_is_idempotent(settings, migrated_db_path):
    db = Database(migrated_db_path)
    try:
        await checkins.create_definition(
            db, checkins_schema(_def_payload(times=("09:00", "09:15")))
        )
        now = datetime(2026, 10, 19, 9, 30, tzinfo=UTC)  # both due, both within window
        await checkins.tick(settings, db, now)
        assert await _counts(db) == 2
        await checkins.tick(settings, db, now)  # duplicate tick -> no new instances
        assert await _counts(db) == 2
    finally:
        await db.dispose()


async def test_tick_skips_expired_occurrences(settings, migrated_db_path):
    db = Database(migrated_db_path)
    try:
        await checkins.create_definition(
            db, checkins_schema(_def_payload(times=("09:00",), due_window_minutes=10))
        )
        now = datetime(2026, 10, 19, 12, 0, tzinfo=UTC)
        await checkins.tick(settings, db, now)
        assert await _counts(db) == 0  # 09:00 + 10 min < 12:00 -> expired on arrival
    finally:
        await db.dispose()


async def test_tick_weekly_respects_weekdays(settings, migrated_db_path):
    db = Database(migrated_db_path)
    try:
        # 2026-10-19 is a Monday. Only days [1] should produce an instance today.
        payload = _def_payload(
            times=("08:00",), schedule={"repeat": "weekly", "days": [1, 7], "times": ["08:00"]}
        )
        await checkins.create_definition(db, checkins_schema(payload))
        now = datetime(2026, 10, 19, 9, 0, tzinfo=UTC)
        await checkins.tick(settings, db, now)
        assert await _counts(db) == 1
    finally:
        await db.dispose()


async def test_tick_once_auto_deactivates(settings, migrated_db_path):
    db = Database(migrated_db_path)
    try:
        payload = _def_payload(schedule={"repeat": "once", "scheduled_at": "2026-10-19T08:00"})
        await checkins.create_definition(db, checkins_schema(payload))
        now = datetime(2026, 10, 19, 9, 0, tzinfo=UTC)
        await checkins.tick(settings, db, now)
        assert await _counts(db) == 1
        defs = await checkins.list_definitions(db)
        assert defs[0]["active"] is False
        # a later tick must not create it again
        await checkins.tick(settings, db, datetime(2026, 10, 20, 9, 0, tzinfo=UTC))
        assert await _counts(db) == 1
    finally:
        await db.dispose()


async def test_tick_marks_missed_at_boundary(settings, migrated_db_path):
    db = Database(migrated_db_path)
    try:
        await checkins.create_definition(
            db, checkins_schema(_def_payload(times=("09:00",), due_window_minutes=60))
        )
        due_at = datetime(2026, 10, 19, 9, 0, tzinfo=UTC)
        # create the instance first
        await checkins.tick(settings, db, due_at)
        assert await _counts(db, "pending") == 1
        # exactly at the boundary (09:00 + 60 min = 10:00) -> must be missed
        await checkins.tick(settings, db, datetime(2026, 10, 19, 10, 0, tzinfo=UTC))
        assert await _counts(db, "missed") == 1
        assert await _counts(db, "pending") == 0
    finally:
        await db.dispose()


async def test_tick_timezone_conversion(settings, migrated_db_path):
    # Europe/Berlin in summer is UTC+2: a local 09:00 must become 07:00Z.
    tz_settings = settings.model_copy(update={"tz": "Europe/Berlin", "enable_scheduler": False})
    db = Database(migrated_db_path)
    try:
        await checkins.create_definition(db, checkins_schema(_def_payload(times=("09:00",))))
        now = datetime(2026, 7, 1, 7, 30, tzinfo=UTC)  # summer, shortly after 09:00 Berlin
        await checkins.tick(tz_settings, db, now)
        assert await _counts(db) == 1
        from sqlalchemy import select

        from guai_hub.models import CheckinInstance

        async with db.session_factory() as s:
            inst = (await s.execute(select(CheckinInstance).limit(1))).scalar_one()
            assert inst.scheduled_local == "2026-07-01T09:00:00"
            assert inst.scheduled_utc == "2026-07-01T07:00:00Z"
    finally:
        await db.dispose()


# --- Device endpoints ------------------------------------------------------


def test_pending_requires_token(client):
    assert client.get("/api/checkins/pending").status_code == 401


def test_answer_lifecycle(client, settings, migrated_db_path):
    import asyncio

    client.post("/api/checkins", json=_def_payload()).json()
    db = Database(migrated_db_path)
    asyncio.run(checkins.tick(settings, db, datetime(2026, 10, 19, 9, 0, tzinfo=UTC)))
    asyncio.run(db.dispose())

    pending = client.get("/api/checkins/pending", headers=auth()).json()
    assert len(pending) == 1
    item = pending[0]
    assert item["answer_type"] == "boolean"
    assert item["options"] is None and item["scale"] is None
    assert item["due_by"] > item["scheduled_at"]

    instance_id = item["instance_id"]

    # idempotent submission
    ok = client.post(
        f"/api/checkins/instances/{instance_id}/responses",
        json={"boolean": True},
        headers=auth(),
    )
    assert ok.status_code == 200
    assert ok.json()["value"] == {"boolean": True}
    again = client.post(
        f"/api/checkins/instances/{instance_id}/responses",
        json={"boolean": True},
        headers=auth(),
    )
    assert again.status_code == 200 and again.json()["value"] == {"boolean": True}

    # conflicting duplicate
    conflict = client.post(
        f"/api/checkins/instances/{instance_id}/responses",
        json={"boolean": False},
        headers=auth(),
    )
    assert conflict.status_code == 409

    # no longer pending
    assert client.get("/api/checkins/pending", headers=auth()).json() == []

    # unknown instance
    assert (
        client.post(
            "/api/checkins/instances/does-not-exist/responses",
            json={"boolean": True},
            headers=auth(),
        ).status_code
        == 404
    )


def test_answer_types_and_validation(client, settings, migrated_db_path):
    import asyncio

    client.post(
        "/api/checkins",
        json=_def_payload(times=("09:00",), answer_type="scale", scale_min=1, scale_max=10),
    )
    client.post(
        "/api/checkins",
        json=_def_payload(
            times=("09:00",),
            answer_type="single_choice",
            options=["No headache", "Mild", "Severe"],
        ),
    )

    db = Database(migrated_db_path)
    asyncio.run(checkins.tick(settings, db, datetime(2026, 10, 19, 9, 0, tzinfo=UTC)))
    asyncio.run(db.dispose())

    pending = client.get("/api/checkins/pending", headers=auth()).json()
    scale_item = next(p for p in pending if p["answer_type"] == "scale")
    choice_item = next(p for p in pending if p["answer_type"] == "single_choice")

    assert scale_item["scale"] == {"min": 1, "max": 10}
    assert choice_item["options"] == ["No headache", "Mild", "Severe"]
    assert choice_item["scale"] is None

    # scale out of range -> 422
    bad = client.post(
        f"/api/checkins/instances/{scale_item['instance_id']}/responses",
        json={"scale": 11},
        headers=auth(),
    )
    assert bad.status_code == 422

    # wrong-shaped body -> 422
    empty = client.post(
        f"/api/checkins/instances/{choice_item['instance_id']}/responses",
        json={},
        headers=auth(),
    )
    assert empty.status_code == 422

    # valid choice -> 200
    ok = client.post(
        f"/api/checkins/instances/{choice_item['instance_id']}/responses",
        json={"choice": "Mild"},
        headers=auth(),
    )
    assert ok.status_code == 200
    assert ok.json()["value"] == {"choice": "Mild"}


def test_answer_after_missed_is_410(client, settings, migrated_db_path):
    import asyncio

    client.post("/api/checkins", json=_def_payload(due_window_minutes=5)).json()
    db = Database(migrated_db_path)
    # instance due at 09:00 with a 5 min window; at 09:10 it is missed
    asyncio.run(checkins.tick(settings, db, datetime(2026, 10, 19, 9, 0, tzinfo=UTC)))
    asyncio.run(checkins.tick(settings, db, datetime(2026, 10, 19, 9, 10, tzinfo=UTC)))
    asyncio.run(db.dispose())

    pending = client.get("/api/checkins/pending", headers=auth()).json()
    assert pending == []

    inst = client.get("/api/checkin-instances").json()[0]
    assert inst["status"] == "missed"

    resp = client.post(
        f"/api/checkins/instances/{inst['id']}/responses",
        json={"boolean": True},
        headers=auth(),
    )
    assert resp.status_code == 410


def checkins_schema(payload):
    from guai_hub.schemas import CheckinWrite

    return CheckinWrite.model_validate(payload)
