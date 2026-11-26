"""Metric selection tests (1.7.0 step 6 gate): registry, settings round-trip,
orphan pruning, and the /forecasts/metrics API."""

import json

from guai_hub.database import Database
from guai_hub.forecasting.features import MetricSpec
from guai_hub.forecasting.selection import (
    FORECAST_METRICS_KEY,
    build_registry,
    get_selected_metrics,
    prune_orphan_forecasts,
    resolve_selection,
    set_selected_metrics,
)
from guai_hub.models import CheckinDefinition, ForecastResult

SENSOR_METRICS = {"steps", "resting_bpm", "sleep_seconds"}


def definition(def_id: str, answer_type: str, active: bool = True) -> CheckinDefinition:
    return CheckinDefinition(
        id=def_id,
        title=f"Q-{def_id}",
        answer_type=answer_type,
        scale_min=0,
        scale_max=10,
        options='["Good", "Neutral", "Bad"]' if answer_type == "single_choice" else None,
        schedule="{}",
        due_window_minutes=120,
        reminder_minutes=30,
        active=active,
        created_at="2026-10-20T00:00:00Z",
        updated_at="2026-10-20T00:00:00Z",
    )


def test_registry_covers_sensors_and_active_checkins_only():
    """User-defined check-ins become metrics dynamically; inactive ones drop out."""
    entries = build_registry(
        [
            definition("bool-1", "boolean"),
            definition("scale-1", "scale"),
            definition("choice-1", "single_choice"),
            definition("gone-1", "boolean", active=False),
        ]
    )
    metrics = {e.spec.metric for e in entries}
    assert SENSOR_METRICS <= metrics
    assert "checkin:bool-1" in metrics
    assert "checkin:scale-1" in metrics
    assert "checkin:choice-1" in metrics
    assert "checkin:gone-1" not in metrics, "inactive definitions are not forecastable"


def test_registry_kinds_match_answer_types():
    """boolean/single_choice -> classification; scale and sensors -> regression."""
    entries = build_registry(
        [
            definition("bool-1", "boolean"),
            definition("scale-1", "scale"),
            definition("choice-1", "single_choice"),
        ]
    )
    kinds = {e.spec.metric: e.kind for e in entries}
    assert kinds["checkin:bool-1"] == "classification"
    assert kinds["checkin:scale-1"] == "regression"
    assert kinds["steps"] == "regression"
    # labels come from the definition title for check-ins
    labels = {e.spec.metric: e.label for e in entries}
    assert labels["checkin:bool-1"] == "Q-bool-1"
    assert labels["checkin:scale-1"] == "Q-scale-1"
    # class labels for probability display: boolean -> No/Yes, choice -> options
    classes = {e.spec.metric: e.classes for e in entries}
    assert classes["checkin:bool-1"] == ["No", "Yes"]
    assert classes["checkin:choice-1"] == ["Good", "Neutral", "Bad"]
    assert classes["steps"] is None


def test_checkin_scale_spec_is_regression_shaped():
    """Scale check-ins forecast as regression: same lag/dow features as sensors."""
    entries = build_registry([definition("scale-1", "scale")])
    entry = next(e for e in entries if e.spec.metric == "checkin:scale-1")
    assert isinstance(entry.spec, MetricSpec)
    assert entry.spec.features == ("target_lag1", "dow")
    assert entry.classes is None, "scale forecasts are point values, not class bars"


def test_unset_selection_defaults_to_all(migrated_db_path):
    """Missing settings key means 'all active metrics on' (default)."""
    db = Database(migrated_db_path)
    entries = build_registry([definition("bool-1", "boolean")])
    selected = resolve_selection_sync(entries, db)
    assert {e.spec.metric for e in selected} == SENSOR_METRICS | {"checkin:bool-1"}


def resolve_selection_sync(entries, db):
    import asyncio

    return asyncio.run(resolve_selection(db, entries))


def test_stored_selection_filters_and_ignores_unknown(migrated_db_path):
    db = Database(migrated_db_path)
    entries = build_registry([definition("bool-1", "boolean")])
    import asyncio

    asyncio.run(set_selected_metrics(db, ["steps", "checkin:bool-1", "checkin:deleted"]))
    selected = resolve_selection_sync(entries, db)
    assert {e.spec.metric for e in selected} == {"steps", "checkin:bool-1"}


def test_empty_selection_is_explicit_optout(migrated_db_path):
    """Stored [] means 'nothing forecast' — distinct from unset (all on)."""
    db = Database(migrated_db_path)
    entries = build_registry([definition("bool-1", "boolean")])
    import asyncio

    asyncio.run(set_selected_metrics(db, []))
    assert asyncio.run(get_selected_metrics(db)) == []
    assert resolve_selection_sync(entries, db) == []


def test_prune_orphan_forecasts(migrated_db_path):
    """Rows for deselected metrics and deleted definitions are removed."""
    db = Database(migrated_db_path)

    async def seed(metric: str):
        async with db.session_factory() as s:
            await s.merge(
                ForecastResult(
                    metric=metric,
                    target_date="2026-11-27",
                    horizon=1,
                    model="lag1",
                    value=1.0,
                    reliable=False,
                    computed_at="2026-11-27T05:00:00Z",
                )
            )
            await s.commit()

    import asyncio

    asyncio.run(seed("steps"))  # selected -> survives
    asyncio.run(seed("sleep_seconds"))  # deselected -> pruned
    asyncio.run(seed("checkin:deleted"))  # definition gone -> pruned

    # explicit selection keeps only steps + the live check-in
    asyncio.run(set_selected_metrics(db, ["steps", "checkin:bool-1"]))
    entries = build_registry([definition("bool-1", "boolean")])
    selected = resolve_selection_sync(entries, db)
    pruned = asyncio.run(prune_orphan_forecasts(db, {e.spec.metric for e in selected}))
    assert pruned == 2

    async def remaining():
        from sqlmodel import select

        async with db.session_factory() as s:
            rows = (await s.execute(select(ForecastResult))).scalars().all()
            return {r.metric for r in rows}

    assert asyncio.run(remaining()) == {"steps"}


def test_forecast_metrics_api_roundtrip(client, settings, migrated_db_path):
    import asyncio

    from guai_hub.database import Database

    # seed one check-in definition
    async def _seed():
        db = Database(migrated_db_path)
        async with db.session_factory() as s:
            s.add(definition("bool-1", "boolean"))
            await s.commit()
        await db.dispose()

    asyncio.run(_seed())

    body = client.get("/api/forecasts/metrics").json()
    by_metric = {m["metric"]: m for m in body["metrics"]}
    assert set(by_metric) == SENSOR_METRICS | {"checkin:bool-1"}
    assert all(m["selected"] for m in by_metric.values()), "default: all on"
    assert by_metric["checkin:bool-1"]["kind"] == "classification"
    assert by_metric["checkin:bool-1"]["label"] == "Q-bool-1"

    # opt out of everything but steps
    put = client.put("/api/forecasts/metrics", json={"metrics": ["steps"]})
    assert put.status_code == 200
    by_metric = {m["metric"]: m for m in put.json()["metrics"]}
    assert by_metric["steps"]["selected"] is True
    assert by_metric["checkin:bool-1"]["selected"] is False

    # persisted: a fresh GET reflects the stored selection
    body = client.get("/api/forecasts/metrics").json()
    by_metric = {m["metric"]: m for m in body["metrics"]}
    assert [m["metric"] for m in body["metrics"] if m["selected"]] == ["steps"]

    # the settings key holds exactly what was PUT
    import sqlite3

    value = (
        sqlite3.connect(migrated_db_path)
        .execute("SELECT value FROM settings WHERE key = ?", (FORECAST_METRICS_KEY,))
        .fetchone()[0]
    )
    assert json.loads(value) == ["steps"]
