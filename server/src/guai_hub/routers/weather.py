"""Weather read endpoints (unauthenticated, trusted LAN)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from guai_hub.deps import get_db, require_token
from guai_hub.models import Weather
from guai_hub.services import weather

router = APIRouter()


@router.get("/weather", response_model=list[Weather])
async def weather_history(
    from_: str | None = Query(default=None, alias="from"),
    to: str | None = Query(default=None, alias="to"),
    session: AsyncSession = Depends(get_db),
) -> list[Weather]:
    stmt = select(Weather)
    if from_ is not None:
        stmt = stmt.where(Weather.event_time >= from_)
    if to is not None:
        stmt = stmt.where(Weather.event_time <= to)
    stmt = stmt.order_by(Weather.event_time.desc()).limit(10000)
    rows = (await session.execute(stmt)).scalars().all()
    return list(rows)


@router.get("/weather/current", dependencies=[Depends(require_token)])
async def weather_current(request: Request) -> dict:
    """Device endpoint: latest conditions + today's high/low for the watchface."""
    data = await weather.current(request.app.state.settings, request.app.state.db)
    return {"weather": data}


@router.get("/weather/latest", response_model=Weather | None)
async def weather_latest(session: AsyncSession = Depends(get_db)) -> Weather | None:
    stmt = select(Weather).order_by(Weather.event_time.desc()).limit(1)
    return (await session.execute(stmt)).scalar_one_or_none()
