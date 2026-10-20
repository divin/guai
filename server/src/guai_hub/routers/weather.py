"""Weather history reads (unauthenticated, trusted LAN).

The device-facing ``GET /api/weather/current`` lives in ``routers/device.py`` and is
mounted only in ``full``/``device`` modes.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from guai_hub.deps import get_db
from guai_hub.models import Weather

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


@router.get("/weather/latest", response_model=Weather | None)
async def weather_latest(session: AsyncSession = Depends(get_db)) -> Weather | None:
    stmt = select(Weather).order_by(Weather.event_time.desc()).limit(1)
    return (await session.execute(stmt)).scalar_one_or_none()
