"""Settings table access: the dashboard-configured location is stored here."""

from __future__ import annotations

from typing import Any

from guai_hub.database import Database
from guai_hub.models import KeyValueSetting

_KEY_NAME = "location_name"
_KEY_LAT = "latitude"
_KEY_LON = "longitude"


async def _get(session, key: str) -> str | None:
    row = await session.get(KeyValueSetting, key)
    return row.value if row else None


async def _upsert(session, key: str, value: str) -> None:
    await session.merge(KeyValueSetting(key=key, value=value))


async def get_location(db: Database) -> dict[str, Any] | None:
    """Return the configured location dict or ``None`` if not set."""
    async with db.session_factory() as session:
        name = await _get(session, _KEY_NAME)
        lat = await _get(session, _KEY_LAT)
        lon = await _get(session, _KEY_LON)
    try:
        if name and lat is not None and lon is not None:
            return {
                "name": name,
                "latitude": float(lat),
                "longitude": float(lon),
            }
    except (TypeError, ValueError):  # pragma: no cover - defensive
        return None
    return None


async def set_location(
    db: Database, name: str, latitude: float, longitude: float
) -> dict[str, Any]:
    async with db.session_factory() as session:
        await _upsert(session, _KEY_NAME, name)
        await _upsert(session, _KEY_LAT, str(latitude))
        await _upsert(session, _KEY_LON, str(longitude))
        await session.commit()
    return {"name": name, "latitude": latitude, "longitude": longitude}
