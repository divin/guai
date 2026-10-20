"""Data-ingestion service: apply a watch sync idempotently, section by section.

Each section upserts on its natural key (``event_time`` / ``night_date``) so re-syncing
never creates duplicates. A failure in one section is isolated; see the router for how
partial failures are reported.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from guai_hub.models import Activity, HeartRate, Sleep
from guai_hub.schemas import SyncPayload


async def apply_sync(session: AsyncSession, payload: SyncPayload) -> dict[str, int]:
    """Write all sections of a sync payload. Returns per-section row counts."""
    counts: dict[str, int] = {"activity": 0, "heart_rate": 0, "sleep": 0}

    if payload.activity is not None:
        values = {
            "event_time": payload.collected_at,
            "steps": payload.activity.steps,
            "distance_meters": payload.activity.distance_meters,
            "active_seconds": payload.activity.active_seconds,
            "resting_kcal": payload.activity.resting_kcal,
            "active_kcal": payload.activity.active_kcal,
        }
        stmt = _upsert(Activity, values)
        counts["activity"] += (await session.execute(stmt)).rowcount or 0

    for sample in payload.heart_rates or []:
        values = {
            "event_time": sample.at,
            "bpm": sample.bpm,
            "bpm_raw": sample.bpm_raw,
        }
        stmt = _upsert(HeartRate, values)
        counts["heart_rate"] += (await session.execute(stmt)).rowcount or 0

    for sample in payload.sleep or []:
        values = {
            "night_date": sample.night_date,
            "sleep_seconds": sample.sleep_seconds,
            "restful_seconds": sample.restful_seconds,
        }
        stmt = _upsert(Sleep, values)
        counts["sleep"] += (await session.execute(stmt)).rowcount or 0

    return counts


def _upsert(model, values: dict[str, Any]):
    """SQLite INSERT ... ON CONFLICT DO UPDATE keyed on the model's primary key."""
    table = model.__table__
    pk = [c.name for c in table.primary_key.columns]
    update = {k: v for k, v in values.items() if k not in pk}
    return (
        sqlite_insert(table).values(**values).on_conflict_do_update(index_elements=pk, set_=update)
    )
