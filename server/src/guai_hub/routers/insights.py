"""Insight endpoints (unauthenticated, trusted LAN): daily, stats, correlations, export."""

from __future__ import annotations

from fastapi import APIRouter, Query, Request
from fastapi.responses import Response

from guai_hub.services import insights

router = APIRouter()


@router.get("/daily")
async def daily(
    request: Request,
    from_local: str | None = Query(default=None, alias="from"),
    to_local: str | None = Query(default=None, alias="to"),
):
    return await insights.daily_rows(
        request.app.state.settings,
        request.app.state.db,
        from_local=from_local,
        to_local=to_local,
    )


@router.get("/stats")
async def stats(request: Request) -> dict:
    return await insights.stats(request.app.state.settings, request.app.state.db)


@router.get("/correlations/sleep-vs-checkin")
async def sleep_vs_checkin(request: Request, checkin_id: str):
    return await insights.sleep_vs_checkin(request.app.state.db, checkin_id)


@router.get("/correlations/weather-vs-checkin")
async def weather_vs_checkin(
    request: Request,
    checkin_id: str,
    metric: str = Query(default="pressure_hpa"),
):
    return await insights.weather_vs_checkin(
        request.app.state.settings, request.app.state.db, checkin_id, metric
    )


@router.get("/export.csv")
async def export_csv(request: Request, kind: str = "daily") -> Response:
    filename, media_type, csv_text = await insights.export_csv(
        request.app.state.settings, request.app.state.db, kind
    )
    return Response(
        content=csv_text,
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
