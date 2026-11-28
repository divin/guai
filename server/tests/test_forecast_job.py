"""Nightly forecast job tests (1.7.0 step 7 gate): multi-class baseline,
job end-to-end on a seeded DB, scheduler timing, and the /forecasts API."""

import json
from datetime import UTC, date, datetime, timedelta

import pytest

from guai_hub.config import Settings
from guai_hub.database import Database
from guai_hub.forecasting.job import run_forecast_job
from guai_hub.forecasting.models import ExpandingPriorClassifier
from guai_hub.models import (
    Activity,
    CheckinDefinition,
    CheckinInstance,
    CheckinResponse,
    ForecastResult,
    HeartRate,
    Sleep,
)
from guai_hub.scheduler import seconds_until_forecast

TEST_SETTINGS = Settings(tz="UTC", sync_token="0123456789abcdef")
SENSOR_METRICS = {"steps", "resting_bpm", "sleep_seconds"}
START = date(2026, 10, 20)


def test_expanding_prior_multiclass():
    """Three classes: prior over options; probability rows sum to 1."""
    model = ExpandingPriorClassifier(n_classes=3)
    model.fit([[0.0]] * 6, [0, 0, 1, 1, 2, 2])
    rows = model.predict_proba([[0.0], [1.0]])
    assert rows[0] == pytest.approx([1 / 3, 1 / 3, 1 / 3])
    assert rows[1] == rows[0]
    assert sum(rows[0]) == pytest.approx(1.0)
    with pytest.raises(ValueError):
        model.fit([[0.0]], [3])  # class index out of range


def test_walk_forward_multiclass_scoring():
    """3-class targets: logloss on the true class column, argmax accuracy."""
    from guai_hub.forecasting.backtest import walk_forward_classification

    # 15 days cycling 0,1,2; the prior predicts (1/3,1/3,1/3) -> logloss = ln(3)
    X = [[float(i)] for i in range(15)]
    y = [float(i % 3) for i in range(15)]
    scores = walk_forward_classification(
        X, y, {"prior": lambda: ExpandingPriorClassifier(n_classes=3)}, warmup=6
    )
    # folds see imbalanced cycling targets, so the loss hovers just above ln(3)
    assert 1.0 <= scores["prior"]["logloss"] <= 1.35
    assert 0.2 <= scores["prior"]["accuracy"] <= 0.5


def test_walk_forward_drops_broken_candidate():
    """A candidate raising at any fold is dropped; the rest keep their rows."""
    from guai_hub.forecasting.backtest import walk_forward_classification
    from guai_hub.forecasting.models import new_classifier

    def broken():
        model = new_classifier("expanding_prior")
        model.predict_proba = lambda features: (_ for _ in ()).throw(RuntimeError("boom"))
        return model

    X = [[float(i % 2)] for i in range(15)]
    y = [float(i % 2) for i in range(15)]
    scores = walk_forward_classification(
        X, y, {"prior": lambda: new_classifier("expanding_prior"), "bad": broken}, warmup=5
    )
    assert "bad" not in scores
    assert scores["prior"]["n"] == 10


def _seed_db(db_path: str, days: int = 30) -> None:
    """Seed closed days of activity/HR plus boolean and scale check-in answers.

    Days end *yesterday*: the job forecasts today from closed history, so the
    seed must provide a closed lag-1 day.
    """
    import asyncio

    db = Database(db_path)
    start = date.today() - timedelta(days=days)

    async def _seed():
        async with db.session_factory() as s:
            for i in range(days):
                day = start + timedelta(days=i)
                # cumulative counters: midday value, final value synced 23:30
                await s.merge(Activity(event_time=f"{day}T12:00:00Z", steps=100 * (i + 1)))
                await s.merge(Activity(event_time=f"{day}T23:30:00Z", steps=100 * (i + 2)))
                # dense HR: 100 samples/day so the resting-HR target is usable
                for k in range(100):
                    await s.merge(
                        HeartRate(
                            event_time=f"{day}T{0 + k // 60:02d}:{k % 60:02d}:00Z",
                            bpm=60 + (i % 5),
                        )
                    )
                # sleep: one row per night, ~7h restful-varied
                await s.merge(
                    Sleep(
                        night_date=day.isoformat(),
                        sleep_seconds=25200 + (i % 4) * 1800,
                        restful_seconds=14400,
                    )
                )
            s.add(
                CheckinDefinition(
                    id="def-bool",
                    title="Headache?",
                    answer_type="boolean",
                    scale_min=0,
                    scale_max=1,
                    schedule="{}",
                    due_window_minutes=120,
                    reminder_minutes=30,
                    active=True,
                    created_at="2026-10-20T00:00:00Z",
                    updated_at="2026-10-20T00:00:00Z",
                )
            )
            s.add(
                CheckinDefinition(
                    id="def-scale",
                    title="Energy",
                    answer_type="scale",
                    scale_min=0,
                    scale_max=10,
                    schedule="{}",
                    due_window_minutes=120,
                    reminder_minutes=30,
                    active=True,
                    created_at="2026-10-20T00:00:00Z",
                    updated_at="2026-10-20T00:00:00Z",
                )
            )
            s.add(
                CheckinDefinition(
                    id="def-choice",
                    title="Day quality",
                    answer_type="single_choice",
                    scale_min=0,
                    scale_max=10,
                    options='["Good", "Neutral", "Bad"]',
                    schedule="{}",
                    due_window_minutes=120,
                    reminder_minutes=30,
                    active=True,
                    created_at="2026-10-20T00:00:00Z",
                    updated_at="2026-10-20T00:00:00Z",
                )
            )
            await s.commit()

        async with db.session_factory() as s:
            for i in range(days):
                day = start + timedelta(days=i)
                for def_id in ("def-bool", "def-scale", "def-choice"):
                    inst = CheckinInstance(
                        id=f"inst-{def_id}-{i}",
                        definition_id=def_id,
                        scheduled_local=f"{day}T21:00:00",
                        scheduled_utc=f"{day}T21:00:00Z",
                        status="answered",
                    )
                    s.add(inst)
                    if def_id == "def-bool":
                        resp = CheckinResponse(
                            id=f"resp-{def_id}-{i}",
                            instance_id=inst.id,
                            value_boolean=i % 2,
                            submitted_at=f"{day}T20:30:00Z",
                        )
                    elif def_id == "def-scale":
                        resp = CheckinResponse(
                            id=f"resp-{def_id}-{i}",
                            instance_id=inst.id,
                            value_scale=5 + (i % 3),
                            submitted_at=f"{day}T20:30:00Z",
                        )
                    else:
                        # single_choice where "Bad" is never answered: the model's
                        # observed classes are a subset of the 3 options
                        resp = CheckinResponse(
                            id=f"resp-{def_id}-{i}",
                            instance_id=inst.id,
                            value_choice="Good" if i % 2 == 0 else "Neutral",
                            submitted_at=f"{day}T20:30:00Z",
                        )
                    s.add(resp)
            await s.commit()

    asyncio.run(_seed())
    asyncio.run(db.dispose())


def _stored(db_path: str) -> list[ForecastResult]:
    import asyncio

    from sqlmodel import select

    db = Database(db_path)

    async def _all():
        async with db.session_factory() as s:
            return list((await s.execute(select(ForecastResult))).scalars().all())

    rows = asyncio.run(_all())
    asyncio.run(db.dispose())
    return rows


def test_run_forecast_job_end_to_end(migrated_db_path):
    """Selected metrics get stored forecast rows with correct shapes and tags."""
    import asyncio

    _seed_db(migrated_db_path)
    db = Database(migrated_db_path)

    async def run():
        return await run_forecast_job(TEST_SETTINGS, db)

    summary = asyncio.run(run())
    stored = _stored(migrated_db_path)
    asyncio.run(db.dispose())

    # default selection: 3 sensors + 2 check-ins (all selected, all forecast)
    # default selection: 3 sensors + 3 check-ins
    assert set(summary["per_metric"]) == SENSOR_METRICS | {
        "checkin:def-bool",
        "checkin:def-scale",
        "checkin:def-choice",
    }
    assert summary["rows"] == len(stored) > 0

    by_metric: dict[str, list[ForecastResult]] = {}
    for row in stored:
        by_metric.setdefault(row.metric, []).append(row)

    # regression metrics (sensors + scale check-in): point values, no probabilities
    for metric in SENSOR_METRICS | {"checkin:def-scale"}:
        h1 = [r for r in by_metric[metric] if r.horizon == 1]
        assert len(h1) == 1, metric
        assert h1[0].value is not None
        assert h1[0].probabilities is None
        if h1[0].backtest_score is not None:
            assert h1[0].baseline_score is not None
        # reliable iff a non-baseline model strictly beat the best baseline
        assert h1[0].reliable == (
            h1[0].backtest_score is not None and h1[0].backtest_score < h1[0].baseline_score
        )

    # boolean check-in: classification with probabilities JSON over {0,1}
    h1 = next(r for r in by_metric["checkin:def-bool"] if r.horizon == 1)
    assert h1.value is None
    probs = json.loads(h1.probabilities)
    assert set(probs) == {"0", "1"}
    assert abs(sum(probs.values()) - 1.0) < 1e-3

    # single_choice with "Bad" never answered: probability keys must be the
    # model's ACTUAL class indices (a subset of 0..2), never positions —
    # this is what keeps the UI's Good/Neutral/Bad labels truthful
    choice_h1 = next(r for r in by_metric["checkin:def-choice"] if r.horizon == 1)
    choice_probs = json.loads(choice_h1.probabilities)
    assert set(choice_probs) and all(0 <= int(k) <= 2 for k in choice_probs)
    assert abs(sum(choice_probs.values()) - 1.0) < 1e-3
    if choice_h1.model == "inpriora_classifier":
        assert "2" not in choice_probs, "class absent from training must be absent from keys"


def test_run_forecast_job_prunes_deselected(migrated_db_path):
    """Running the job after narrowing the selection removes stale rows."""
    import asyncio

    from guai_hub.forecasting.selection import set_selected_metrics

    _seed_db(migrated_db_path)
    db = Database(migrated_db_path)
    asyncio.run(run_forecast_job(TEST_SETTINGS, db))

    asyncio.run(set_selected_metrics(db, ["steps"]))
    asyncio.run(run_forecast_job(TEST_SETTINGS, db))

    import asyncio as aio

    from sqlmodel import select

    async def metrics():
        async with db.session_factory() as s:
            rows = (await s.execute(select(ForecastResult))).scalars().all()
            return {r.metric for r in rows}

    assert aio.run(metrics()) == {"steps"}
    asyncio.run(db.dispose())


def test_seconds_until_forecast():
    now = datetime(2026, 11, 27, 5, 0, 0, tzinfo=UTC)
    assert seconds_until_forecast(now, 6) == 3600.0
    assert seconds_until_forecast(now.replace(hour=7), 6) == 23 * 3600.0, "next occurrence tomorrow"
    assert seconds_until_forecast(now.replace(hour=6), 6) == 24 * 3600.0


def test_forecast_hour_setting(monkeypatch):
    monkeypatch.setenv("TZ", "UTC")
    monkeypatch.setenv("GUAI_SYNC_TOKEN", "0123456789abcdef")
    monkeypatch.setenv("GUAI_FORECAST_HOUR", "9")
    assert Settings().forecast_hour == 9
    monkeypatch.delenv("GUAI_FORECAST_HOUR")
    assert Settings().forecast_hour == 6


def test_forecasts_api_serves_stored_rows(client, settings, migrated_db_path):
    import asyncio

    from guai_hub.database import Database
    from guai_hub.models import ForecastResult

    async def _seed():
        db = Database(migrated_db_path)
        async with db.session_factory() as s:
            s.add(
                ForecastResult(
                    metric="steps",
                    target_date="2026-11-27",
                    horizon=1,
                    model="lag1",
                    value=8123.0,
                    reliable=False,
                    backtest_score=1394.0,
                    baseline_score=1394.0,
                    computed_at="2026-11-27T05:00:00Z",
                )
            )
            await s.commit()
        await db.dispose()

    asyncio.run(_seed())

    body = client.get("/api/forecasts").json()
    assert len(body["forecasts"]) == 1
    row = body["forecasts"][0]
    assert row["metric"] == "steps"
    assert row["value"] == 8123.0
    assert row["reliable"] is False
