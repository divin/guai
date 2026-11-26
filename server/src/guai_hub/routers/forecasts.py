"""Forecast endpoints: metric selection (stored in settings), stored forecasts.

The metric registry is derived from the active check-in definitions plus the
fixed sensor metrics — never a static list — so user-defined check-ins become
forecastable automatically. Fitting happens only in the nightly job; the API
serves stored rows and the selection state.
"""

from __future__ import annotations

from fastapi import APIRouter, Request
from sqlalchemy import select

from guai_hub.forecasting.selection import (
    MetricEntry,
    build_registry,
    resolve_selection,
    set_selected_metrics,
)
from guai_hub.models import CheckinDefinition, ForecastResult
from guai_hub.schemas import (
    ForecastMetricsInput,
    ForecastMetricsResponse,
    ForecastMetricState,
    ForecastResponse,
    ForecastRow,
)

router = APIRouter()


def _to_state(entries: list[MetricEntry], selected: set[str]) -> list[ForecastMetricState]:
    return [
        ForecastMetricState(
            metric=e.spec.metric,
            label=e.label,
            kind=e.kind,
            selected=e.spec.metric in selected,
        )
        for e in entries
    ]


@router.get("/forecasts/metrics", response_model=ForecastMetricsResponse)
async def forecast_metrics(request: Request) -> ForecastMetricsResponse:
    db = request.app.state.db
    async with db.session_factory() as session:
        definitions = list((await session.execute(select(CheckinDefinition))).scalars().all())
    registry = build_registry(definitions)
    resolved = await resolve_selection(db, registry)
    selected = {e.spec.metric for e in resolved}
    return ForecastMetricsResponse(metrics=_to_state(registry, selected))


@router.put("/forecasts/metrics", response_model=ForecastMetricsResponse)
async def put_forecast_metrics(
    input: ForecastMetricsInput, request: Request
) -> ForecastMetricsResponse:
    db = request.app.state.db
    await set_selected_metrics(db, sorted(set(input.metrics)))
    async with db.session_factory() as session:
        definitions = list((await session.execute(select(CheckinDefinition))).scalars().all())
    registry = build_registry(definitions)
    resolved = await resolve_selection(db, registry)
    selected = {e.spec.metric for e in resolved}
    return ForecastMetricsResponse(metrics=_to_state(registry, selected))


@router.get("/forecasts", response_model=ForecastResponse)
async def forecasts(request: Request) -> ForecastResponse:
    """Serve the stored nightly forecasts; fitting never happens here."""
    db = request.app.state.db
    async with db.session_factory() as session:
        rows = list(
            (
                await session.execute(
                    select(ForecastResult).order_by(
                        ForecastResult.metric,
                        ForecastResult.target_date,
                        ForecastResult.horizon,
                    )
                )
            )
            .scalars()
            .all()
        )
    return ForecastResponse(
        forecasts=[
            ForecastRow(
                metric=r.metric,
                target_date=r.target_date,
                horizon=r.horizon,
                model=r.model,
                value=r.value,
                probabilities=r.probabilities,
                reliable=r.reliable,
                backtest_score=r.backtest_score,
                baseline_score=r.baseline_score,
                computed_at=r.computed_at,
            )
            for r in rows
        ]
    )
