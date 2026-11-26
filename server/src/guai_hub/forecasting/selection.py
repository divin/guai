"""Metric selection for the forecasting module.

Check-ins are user-defined, so the set of forecastable metrics is dynamic:
three sensor metrics plus one metric per **active** check-in definition.
The user picks which metrics get forecasts via the ``forecast_metrics``
settings key (a JSON list of metric names):

- key missing -> default: all active metrics on
- key present -> exactly the stored list (unknown names ignored, e.g. a
  deleted check-in definition)
- ``[]`` stored -> explicit opt-out: nothing is forecast

The nightly job prunes ``forecast_results`` rows for metrics that are no
longer selected, so a deleted/deactivated definition leaves no stale rows.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from sqlalchemy import delete, select
from sqlmodel import func

from guai_hub.database import Database
from guai_hub.forecasting.features import (
    RESTING_BPM,
    SLEEP,
    STEPS,
    MetricSpec,
    checkin_spec,
)
from guai_hub.models import CheckinDefinition, ForecastResult, KeyValueSetting

FORECAST_METRICS_KEY = "forecast_metrics"
BOOLEAN_CLASSES = ["No", "Yes"]


@dataclass(frozen=True)
class MetricEntry:
    """One forecastable metric as the UI and job see it."""

    spec: MetricSpec
    label: str  # definition title for check-ins, human name for sensors
    kind: str  # "regression" or "classification"
    classes: list[str] | None = None  # class labels for probability bars; None = point value


def build_registry(definitions: list[CheckinDefinition]) -> list[MetricEntry]:
    """Sensor metrics + one metric per active check-in definition, in order."""
    entries = [
        MetricEntry(STEPS, "Steps", "regression"),
        MetricEntry(RESTING_BPM, "Resting heart rate", "regression"),
        MetricEntry(SLEEP, "Sleep", "regression"),
    ]
    for definition in definitions:
        if not definition.active:
            continue
        if definition.answer_type == "scale":
            entries.append(MetricEntry(checkin_spec(definition.id), definition.title, "regression"))
        elif definition.answer_type == "single_choice":
            options = json.loads(definition.options) if definition.options else []
            entries.append(
                MetricEntry(
                    checkin_spec(definition.id), definition.title, "classification", options
                )
            )
        else:
            entries.append(
                MetricEntry(
                    checkin_spec(definition.id), definition.title, "classification", BOOLEAN_CLASSES
                )
            )
    return entries


async def _get(session, key: str) -> str | None:
    row = await session.get(KeyValueSetting, key)
    return row.value if row else None


async def get_selected_metrics(db: Database) -> list[str] | None:
    """Stored selection, or ``None`` when unset (default: all on)."""
    async with db.session_factory() as session:
        raw = await _get(session, FORECAST_METRICS_KEY)
    if raw is None:
        return None
    value = json.loads(raw)
    return value if isinstance(value, list) else None


async def set_selected_metrics(db: Database, metrics: list[str]) -> list[str]:
    """Persist the selection; returns the stored list."""
    async with db.session_factory() as session:
        await session.merge(KeyValueSetting(key=FORECAST_METRICS_KEY, value=json.dumps(metrics)))
        await session.commit()
    return metrics


async def resolve_selection(db: Database, registry: list[MetricEntry]) -> list[MetricEntry]:
    """Registry filtered by the stored selection (missing key -> all)."""
    stored = await get_selected_metrics(db)
    if stored is None:
        return list(registry)
    wanted = set(stored)
    return [entry for entry in registry if entry.spec.metric in wanted]


async def prune_orphan_forecasts(db: Database, selected_metrics: set[str]) -> int:
    """Delete forecast_results rows whose metric is no longer selected.

    Returns the number of rows removed. Called by the nightly job before it
    writes new forecasts; a deleted/deactivated check-in definition therefore
    never leaves stale rows behind.
    """
    if not selected_metrics:
        async with db.session_factory() as session:
            (count,) = (
                await session.execute(select(func.count()).select_from(ForecastResult))
            ).one()
            await session.execute(delete(ForecastResult))
            await session.commit()
        return count

    async with db.session_factory() as session:
        rows = (await session.execute(select(ForecastResult.metric).distinct())).all()
        orphans = [r[0] for r in rows if r[0] not in selected_metrics]
        deleted = 0
        for metric in orphans:
            result = await session.execute(
                delete(ForecastResult).where(ForecastResult.metric == metric)
            )
            deleted += result.rowcount or 0
        await session.commit()
    return deleted
