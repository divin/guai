"""Forecast storage tests: migration creates forecast_results, upserts, constraints."""

import asyncio
import json

import pytest
from sqlalchemy.exc import IntegrityError

from guai_hub.database import Database
from guai_hub.migrations import run_migrations
from guai_hub.models import ForecastResult


@pytest.fixture
def db_path(tmp_path):
    path = str(tmp_path / "guai.sqlite3")
    run_migrations(path)
    return path


def _forecast(**overrides) -> ForecastResult:
    base: dict = {
        "metric": "steps",
        "target_date": "2026-11-27",
        "horizon": 1,
        "model": "inpriora_regressor",
        "value": 8123.0,
        "reliable": True,
        "backtest_score": 1305.0,
        "baseline_score": 1642.0,
        "computed_at": "2026-11-27T05:00:00Z",
    }
    base.update(overrides)
    return ForecastResult(**base)


async def _save(db: Database, row: ForecastResult) -> None:
    async with db.session_factory() as s:
        await s.merge(row)  # upsert on the natural key
        await s.commit()


def test_migration_creates_forecast_results(db_path):
    import sqlite3

    tables = {
        r[0]
        for r in sqlite3.connect(db_path).execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
    }
    assert "forecast_results" in tables


def test_upsert_on_natural_key(db_path):
    """Re-running the nightly job replaces the day's forecast, no duplicates."""
    db = Database(db_path)
    asyncio.run(_save(db, _forecast(value=8123.0, computed_at="2026-11-27T05:00:00Z")))
    asyncio.run(_save(db, _forecast(value=8450.0, computed_at="2026-11-27T05:30:00Z")))
    asyncio.run(db.dispose())

    read = Database(db_path)
    stored = asyncio.run(_all(read))
    assert len(stored) == 1
    assert stored[0].value == 8450.0
    assert stored[0].computed_at == "2026-11-27T05:30:00Z"


async def _all(db: Database) -> list[ForecastResult]:
    from sqlmodel import select

    async with db.session_factory() as s:
        return list((await s.execute(select(ForecastResult))).scalars().all())


def test_horizon_check_constraint(db_path):
    db = Database(db_path)
    with pytest.raises(IntegrityError):
        asyncio.run(_save(db, _forecast(horizon=4)))
    asyncio.run(db.dispose())


def test_classification_row_with_probabilities(db_path):
    """Check-in forecasts store probabilities instead of a point value."""
    db = Database(db_path)
    asyncio.run(
        _save(
            db,
            _forecast(
                metric="checkin:19f1638b-a004-4006-8489-3358dc7b96f2",
                model="expanding_prior",
                value=None,
                probabilities=json.dumps({"0": 0.64, "1": 0.36}),
                reliable=False,
            ),
        )
    )
    asyncio.run(db.dispose())

    read = Database(db_path)
    stored = asyncio.run(_all(read))[0]
    assert stored.value is None
    assert json.loads(stored.probabilities) == {"0": 0.64, "1": 0.36}
    assert stored.reliable is False
    assert stored.metric.startswith("checkin:")


def test_multiple_metrics_and_horizons_coexist(db_path):
    """(metric, target_date, horizon) keys are independent rows."""
    db = Database(db_path)
    asyncio.run(_save(db, _forecast(horizon=1, value=8123.0)))
    asyncio.run(_save(db, _forecast(horizon=2, value=7900.0)))
    asyncio.run(_save(db, _forecast(metric="resting_bpm", value=42.0)))
    asyncio.run(db.dispose())

    read = Database(db_path)
    stored = asyncio.run(_all(read))
    assert {(s.metric, s.horizon) for s in stored} == {
        ("steps", 1),
        ("steps", 2),
        ("resting_bpm", 1),
    }
