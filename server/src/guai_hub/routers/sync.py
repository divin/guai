"""Device ingest endpoints: ``POST /api/sync`` (token-protected)."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from guai_hub.constants import DEFAULT_NEXT_POLL_SECONDS
from guai_hub.deps import get_db, require_token
from guai_hub.schemas import SyncPayload, SyncResponse
from guai_hub.services.sync import apply_sync
from guai_hub.timeutil import utc_now_iso

router = APIRouter()


@router.post(
    "/sync",
    response_model=SyncResponse,
    dependencies=[Depends(require_token)],
)
async def sync(payload: SyncPayload, session: AsyncSession = Depends(get_db)) -> SyncResponse:
    await apply_sync(session, payload)
    await session.commit()
    return SyncResponse(server_time=utc_now_iso(), next_poll_seconds=DEFAULT_NEXT_POLL_SECONDS)
