"""Schema v1 creation, Alembic idempotency, and constraint enforcement (Step 1 gate)."""

import asyncio

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from guai_hub.database import Database
from guai_hub.migrations import run_migrations

EXPECTED_TABLES = {
    "settings",
    "activity",
    "heart_rate",
    "sleep",
    "weather",
    "checkin_definitions",
    "checkin_instances",
    "checkin_responses",
}

SCHEDULE = '{"repeat": "daily", "times": ["09:00"]}'
UTC_NOW = "2026-01-01T00:00:00Z"

_INSERT_DEF = text(
    "INSERT INTO checkin_definitions"
    " (id, title, answer_type, scale_min, scale_max, options, schedule,"
    "  due_window_minutes, reminder_minutes, active, created_at, updated_at)"
    " VALUES (:id, 'Q', :answer_type, :scale_min, :scale_max, NULL, :schedule,"
    "  :due_window, 0, 1, :created, :created)"
)


async def _add_definition(
    db: Database,
    def_id: str = "def-1",
    answer_type: str = "boolean",
    scale_min=0,
    scale_max=10,
    due_window: int = 60,
) -> None:
    async with db.session_factory() as s:
        await s.execute(
            _INSERT_DEF,
            {
                "id": def_id,
                "answer_type": answer_type,
                "scale_min": scale_min,
                "scale_max": scale_max,
                "schedule": SCHEDULE,
                "due_window": due_window,
                "created": UTC_NOW,
            },
        )
        await s.commit()


async def _add_instance(
    db: Database,
    inst_id: str = "inst-1",
    def_id: str = "def-1",
    scheduled_utc: str = "2026-01-01T09:00:00Z",
    status: str = "pending",
) -> None:
    async with db.session_factory() as s:
        await s.execute(
            text(
                "INSERT INTO checkin_instances"
                " (id, definition_id, scheduled_local, scheduled_utc, status)"
                " VALUES (:id, :def_id, '2026-01-01 09:00', :scheduled_utc, :status)"
            ),
            {"id": inst_id, "def_id": def_id, "scheduled_utc": scheduled_utc, "status": status},
        )
        await s.commit()


async def _table_names(db: Database) -> set[str]:
    async with db.session_factory() as s:
        rows = await s.execute(
            text(
                "SELECT name FROM sqlite_master"
                " WHERE type='table' AND name NOT LIKE 'sqlite_%' AND name != 'alembic_version'"
            )
        )
        return {r[0] for r in rows}


def test_migration_creates_expected_schema(db_path):
    run_migrations(db_path)
    db = Database(db_path)
    try:
        names = asyncio.run(_table_names(db))
        assert EXPECTED_TABLES <= names
    finally:
        asyncio.run(db.dispose())


async def test_migration_is_idempotent_at_head(db_path):
    run_migrations(db_path)
    run_migrations(db_path)  # second run must be a no-op
    db = Database(db_path)
    try:
        assert (await db.ping()) is None
        assert (await _table_names(db)) == EXPECTED_TABLES
    finally:
        await db.dispose()


async def test_foreign_key_enforced(db_path):
    run_migrations(db_path)
    db = Database(db_path)
    try:
        async with db.session_factory() as s:
            with pytest.raises(IntegrityError, match="FOREIGN KEY constraint failed"):
                await s.execute(
                    text(
                        "INSERT INTO checkin_responses (id, instance_id, value_boolean, submitted_at)"
                        " VALUES ('r', 'missing-instance', 1, :now)"
                    ),
                    {"now": UTC_NOW},
                )
                await s.commit()
    finally:
        await db.dispose()


async def test_heart_rate_check_constraint(db_path):
    run_migrations(db_path)
    db = Database(db_path)
    try:
        async with db.session_factory() as s:
            with pytest.raises(IntegrityError, match="ck_hr_bpm"):
                await s.execute(text("INSERT INTO heart_rate (event_time, bpm) VALUES ('x', 999)"))
                await s.commit()
    finally:
        await db.dispose()


async def test_instance_status_check_constraint(db_path):
    run_migrations(db_path)
    db = Database(db_path)
    try:
        await _add_definition(db)
        await _add_instance(db, status="bogus")
        assert False, "bogus instance status should be rejected"
    except IntegrityError as exc:
        assert "ck_inst_status" in str(exc)
    finally:
        await db.dispose()


async def test_unique_instance_per_definition_and_schedule(db_path):
    run_migrations(db_path)
    db = Database(db_path)
    try:
        await _add_definition(db)
        await _add_instance(db)
        await _add_instance(db, inst_id="inst-2")  # same definition + scheduled_utc
        assert False, "duplicate (definition, scheduled_utc) should be rejected"
    except IntegrityError as exc:
        assert "UNIQUE constraint failed" in str(exc)
        assert "checkin_instances" in str(exc)
    finally:
        await db.dispose()


async def test_response_exactly_one_value(db_path):
    run_migrations(db_path)
    db = Database(db_path)
    try:
        await _add_definition(db)
        await _add_instance(db)
        async with db.session_factory() as s:
            with pytest.raises(IntegrityError, match="ck_resp_exactly_one"):
                await s.execute(
                    text(
                        "INSERT INTO checkin_responses (id, instance_id, value_boolean, value_scale,"
                        " submitted_at) VALUES ('r2', 'inst-1', 1, 5, :now)"
                    ),
                    {"now": UTC_NOW},
                )
                await s.commit()
    finally:
        await db.dispose()


async def test_definition_scale_check(db_path):
    run_migrations(db_path)
    db = Database(db_path)
    try:
        await _add_definition(db, answer_type="scale", scale_min=5, scale_max=5)
        assert False, "scale_min == scale_max should be rejected"
    except IntegrityError as exc:
        assert "ck_def_scale_range" in str(exc)
    finally:
        await db.dispose()


async def test_definition_due_window_check(db_path):
    run_migrations(db_path)
    db = Database(db_path)
    try:
        await _add_definition(db, due_window=1)  # below minimum of 5
        assert False, "due_window_minutes below 5 should be rejected"
    except IntegrityError as exc:
        assert "ck_def_due_window" in str(exc)
    finally:
        await db.dispose()
