"""Device-facing endpoints (bearer-token protected) — the exposed instance surface.

Served only in the ``full`` and ``device`` server modes (``GUAI_SERVER_MODE``); the
``internal`` instance never mounts this router. Everything an operator exposes to the
internet lives here and nothing else.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from guai_hub.constants import DEFAULT_NEXT_POLL_SECONDS
from guai_hub.deps import get_db, require_token
from guai_hub.schemas import AnswerPayload, SyncPayload, SyncResponse
from guai_hub.services import checkins, weather
from guai_hub.services.sync import apply_sync
from guai_hub.timeutil import utc_now_iso

router = APIRouter()


@router.post(
    "/sync",
    response_model=SyncResponse,
    dependencies=[Depends(require_token)],
)
async def sync(payload: SyncPayload, session: AsyncSession = Depends(get_db)) -> SyncResponse:
    """Health-data upload from the watchface (offline-buffered, idempotent upserts)."""
    await apply_sync(session, payload)
    await session.commit()
    return SyncResponse(server_time=utc_now_iso(), next_poll_seconds=DEFAULT_NEXT_POLL_SECONDS)


@router.get("/checkins/pending", dependencies=[Depends(require_token)])
async def pending_checkins(request: Request) -> list[dict]:
    """Due check-in instances for the check-in app, one submission allowed each."""
    return await checkins.list_pending(request.app.state.db)


@router.post("/checkins/instances/{instance_id}/responses", dependencies=[Depends(require_token)])
async def submit_answer(
    instance_id: str,
    payload: AnswerPayload,
    request: Request,
) -> dict:
    """Submit one answer (idempotent per instance; late answers get ``410``)."""
    return await checkins.submit_response(request.app.state.db, instance_id, payload)


@router.get("/weather/current", dependencies=[Depends(require_token)])
async def weather_current(request: Request) -> dict:
    """Latest conditions + today's high/low for the watchface (or ``null``)."""
    data = await weather.current(request.app.state.settings, request.app.state.db)
    return {"weather": data}
