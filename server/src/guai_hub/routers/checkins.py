"""Check-in router.

Dashboard CRUD + reads (unauthenticated, trusted LAN) and the two device endpoints
(bearer-token protected): pending list and answer submission.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request

from guai_hub.deps import require_token
from guai_hub.schemas import AnswerPayload, CheckinWrite
from guai_hub.services import checkins

router = APIRouter()

# --- Dashboard (LAN) -------------------------------------------------------------


@router.get("/checkins")
async def list_checkins(request: Request) -> list[dict]:
    return await checkins.list_definitions(request.app.state.db)


@router.post("/checkins", status_code=201)
async def create_checkin(payload: CheckinWrite, request: Request) -> dict:
    return await checkins.create_definition(request.app.state.db, payload)


@router.get(
    "/checkins/pending", dependencies=[Depends(require_token)]
)  # must stay before /checkins/{checkin_id}
async def pending_checkins(request: Request) -> list[dict]:
    return await checkins.list_pending(request.app.state.db)


@router.get("/checkins/{checkin_id}")
async def get_checkin(checkin_id: str, request: Request) -> dict:
    return await checkins.get_definition(request.app.state.db, checkin_id)


@router.put("/checkins/{checkin_id}")
async def put_checkin(checkin_id: str, payload: CheckinWrite, request: Request) -> dict:
    return await checkins.update_definition(request.app.state.db, checkin_id, payload)


@router.delete("/checkins/{checkin_id}", status_code=204)
async def delete_checkin(checkin_id: str, request: Request) -> None:
    await checkins.delete_definition(request.app.state.db, checkin_id)


@router.post("/checkins/{checkin_id}/deactivate")
async def deactivate_checkin(checkin_id: str, request: Request) -> dict:
    return await checkins.set_active(request.app.state.db, checkin_id, False)


@router.post("/checkins/{checkin_id}/activate")
async def activate_checkin(checkin_id: str, request: Request) -> dict:
    return await checkins.set_active(request.app.state.db, checkin_id, True)


@router.get("/checkin-instances")
async def list_instances(
    request: Request,
    definition_id: str | None = None,
    status: str | None = None,
    from_local: str | None = Query(default=None, alias="from"),
    to_local: str | None = Query(default=None, alias="to"),
):
    return await checkins.list_instances(
        request.app.state.db,
        definition_id=definition_id,
        status=status,
        from_local=from_local,
        to_local=to_local,
    )


@router.get("/checkin-responses")
async def list_responses(
    request: Request,
    definition_id: str | None = None,
    from_utc: str | None = Query(default=None, alias="from"),
    to_utc: str | None = Query(default=None, alias="to"),
):
    return await checkins.list_responses(
        request.app.state.db,
        definition_id=definition_id,
        from_utc=from_utc,
        to_utc=to_utc,
    )


# --- Device endpoints (token-protected) ------------------------------------------


@router.post("/checkins/instances/{instance_id}/responses", dependencies=[Depends(require_token)])
async def submit_answer(
    instance_id: str,
    payload: AnswerPayload,
    request: Request,
) -> dict:
    return await checkins.submit_response(request.app.state.db, instance_id, payload)
