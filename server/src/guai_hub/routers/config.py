"""Dashboard config + location endpoints (unauthenticated, trusted LAN)."""

from __future__ import annotations

import asyncio
import logging
from importlib.metadata import PackageNotFoundError, version

import httpx
from fastapi import APIRouter, HTTPException, Query, Request

from guai_hub.database import Database
from guai_hub.schemas import ConfigResponse, GeoResult, LocationInput, LocationResult
from guai_hub.services import weather
from guai_hub.services.settings_repo import get_location, set_location

logger = logging.getLogger(__name__)
router = APIRouter()

GEOCODING_URL = "https://geocoding-api.open-meteo.com/v1/search"


def _server_version() -> str:
    try:
        return version("guai-hub")
    except PackageNotFoundError:  # pragma: no cover - package is installed in practice
        return "dev"


@router.get("/config", response_model=ConfigResponse)
async def config(request: Request) -> ConfigResponse:
    location = await get_location(request.app.state.db)
    return ConfigResponse(location=location, server_version=_server_version())


@router.get("/geo/search", response_model=list[GeoResult])
async def geo_search(q: str = Query(min_length=1, max_length=200)) -> list[GeoResult]:
    client = httpx.AsyncClient(timeout=15)
    try:
        resp = await client.get(
            GEOCODING_URL,
            params={"name": q, "count": 10, "language": "en", "format": "json"},
        )
        resp.raise_for_status()
    except httpx.HTTPError as exc:
        logger.warning("geo search failed: %s", exc)
        raise HTTPException(status_code=502, detail="geocoding service unavailable") from exc
    finally:
        await client.aclose()

    results = resp.json().get("results", [])
    return [
        GeoResult(
            name=item.get("name"),
            latitude=item.get("latitude"),
            longitude=item.get("longitude"),
            country=item.get("country"),
            admin1=item.get("admin1"),
        )
        for item in results
    ]


@router.put("/config/location", response_model=LocationResult)
async def put_location(input: LocationInput, request: Request) -> LocationResult:
    db: Database = request.app.state.db
    stored = await set_location(db, input.name, input.latitude, input.longitude)
    # Trigger an immediate weather poll; failures are logged by poll machinery.
    asyncio.create_task(weather.poll(request.app.state.settings, db))
    return LocationResult(**stored)
