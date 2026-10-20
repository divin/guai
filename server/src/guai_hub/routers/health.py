"""Health data read endpoints (unauthenticated, trusted LAN).

GET /api/activity, /api/sleep, /api/heart-rate — optional UTC ``from``/``to`` range,
oldest-first ordering (the watch data uploads chronologically).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from guai_hub.deps import get_db
from guai_hub.models import Activity, HeartRate, Sleep

router = APIRouter()


def _range(stmt, model, from_: str | None, to: str | None):
    if from_ is not None:
        stmt = stmt.where(model.event_time >= from_)
    if to is not None:
        stmt = stmt.where(model.event_time <= to)
    return stmt.order_by(model.event_time)


@router.get("/activity", response_model=list[Activity])
async def activity_history(
    from_: str | None = Query(default=None, alias="from"),
    to: str | None = Query(default=None, alias="to"),
    limit: int = Query(default=10000, ge=1, le=100000),
    session: AsyncSession = Depends(get_db),
) -> list[Activity]:
    stmt = _range(select(Activity), Activity, from_, to).limit(limit)
    return list((await session.execute(stmt)).scalars().all())


@router.get("/sleep", response_model=list[Sleep])
async def sleep_history(
    from_: str | None = Query(default=None, alias="from"),
    to: str | None = Query(default=None, alias="to"),
    session: AsyncSession = Depends(get_db),
) -> list[Sleep]:
    stmt = select(Sleep)
    if from_ is not None:
        stmt = stmt.where(Sleep.night_date >= from_)
    if to is not None:
        stmt = stmt.where(Sleep.night_date <= to)
    stmt = stmt.order_by(Sleep.night_date)
    return list((await session.execute(stmt)).scalars().all())


@router.get("/heart-rate", response_model=list[HeartRate])
async def heart_rate_history(
    from_: str | None = Query(default=None, alias="from"),
    to: str | None = Query(default=None, alias="to"),
    limit: int = Query(default=5000, ge=1, le=100000),
    session: AsyncSession = Depends(get_db),
) -> list[HeartRate]:
    stmt = _range(select(HeartRate), HeartRate, from_, to).limit(limit)
    return list((await session.execute(stmt)).scalars().all())
