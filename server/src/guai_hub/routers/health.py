"""Health data read endpoints (unauthenticated, trusted LAN).

GET /api/activity, /api/sleep, /api/heart-rate — optional UTC ``from``/``to`` range,
oldest-first ordering (the watch data uploads chronologically). The ``limit`` row cap on
activity/heart-rate keeps the *newest* rows of the requested range (dashboard "today"
and live views need the most recent samples), still returned oldest-first.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from guai_hub.deps import get_db
from guai_hub.models import Activity, HeartRate, Sleep

router = APIRouter()


def _range(stmt, model, from_: str | None, to: str | None):
    """UTC event_time window. Date-only bounds (10 chars) are inclusive on the whole
    day: comparing full ISO timestamps lexicographically would drop the `to` day's
    rows ("…T09:00:00Z" > "…"), so date-only bounds match on the date prefix."""
    day = func.substr(model.event_time, 1, 10)
    if from_ is not None:
        stmt = stmt.where(day >= from_ if len(from_) == 10 else model.event_time >= from_)
    if to is not None:
        stmt = stmt.where(day <= to if len(to) == 10 else model.event_time <= to)
    return stmt


async def _recent_first(session: AsyncSession, stmt, model, limit: int):
    """Newest ``limit`` rows of the statement's range, returned oldest-first."""
    rows = list(
        (await session.execute(stmt.order_by(model.event_time.desc()).limit(limit))).scalars().all()
    )
    rows.reverse()
    return rows


@router.get("/activity", response_model=list[Activity])
async def activity_history(
    from_: str | None = Query(default=None, alias="from"),
    to: str | None = Query(default=None, alias="to"),
    limit: int = Query(default=10000, ge=1, le=100000),
    session: AsyncSession = Depends(get_db),
) -> list[Activity]:
    return await _recent_first(
        session, _range(select(Activity), Activity, from_, to), Activity, limit
    )


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
    return await _recent_first(
        session, _range(select(HeartRate), HeartRate, from_, to), HeartRate, limit
    )
