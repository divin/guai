"""Check-in engine: definitions CRUD, the TZ-aware one-minute tick (instance
materialisation + missed marking + Pushover), pending/answer device APIs.

Hours are wall-clock in ``TZ``; timestamps are stored in UTC (and a local copy for
scheduling keys). Everything is idempotent: instances dedupe on
``(definition_id, scheduled_utc)``, responses on ``instance_id``.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime, timedelta
from datetime import time as dtime

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from guai_hub.config import Settings
from guai_hub.database import Database
from guai_hub.models import CheckinDefinition, CheckinInstance, CheckinResponse
from guai_hub.schemas import AnswerPayload, CheckinWrite, Schedule
from guai_hub.services import pushover
from guai_hub.timeutil import utc_now_iso

logger = logging.getLogger(__name__)

LOOKBACK_DAYS = 3  # how far back the tick scans for missed occurrences (startup resilience)

REPEAT_DAILY = "daily"
REPEAT_WEEKLY = "weekly"
REPEAT_ONCE = "once"

STATUS_PENDING = "pending"
STATUS_ANSWERED = "answered"
STATUS_MISSED = "missed"

# --- Time helpers ----------------------------------------------------------


def to_utc_iso(local_dt: datetime, tz) -> str:
    """Attach the configured tz to a naive local datetime and return ISO-Z."""
    aware = local_dt.replace(tzinfo=tz).astimezone(UTC)
    return aware.isoformat().replace("+00:00", "Z")


def from_utc_iso(value: str) -> datetime:
    return datetime.fromisoformat(value)


def _parse_time(hhmm: str) -> dtime:
    return datetime.strptime(hhmm, "%H:%M").time()


def occurrences(schedule: Schedule, start_local: datetime, end_local: datetime) -> list[datetime]:
    """All naive-local datetimes for this schedule in [start_local, end_local]."""
    result: list[datetime] = []
    if schedule.repeat in (REPEAT_DAILY, REPEAT_WEEKLY):
        weekdays = None if schedule.repeat == REPEAT_DAILY else set(schedule.days or [])
        day = start_local.date()
        end_date = end_local.date()
        while day <= end_date:
            if weekdays is None or day.isoweekday() in weekdays:
                for hhmm in schedule.times or []:
                    local_dt = datetime.combine(day, _parse_time(hhmm))
                    if start_local <= local_dt <= end_local:
                        result.append(local_dt)
            day += timedelta(days=1)
    else:  # once
        once = datetime.strptime(schedule.scheduled_at or "", "%Y-%m-%dT%H:%M")
        if start_local <= once <= end_local:
            result.append(once)
    return result


# --- Definitions (dashboard CRUD) -----------------------------------------


async def create_definition(db: Database, payload: CheckinWrite) -> dict:
    now = utc_now_iso()
    row = CheckinDefinition(
        title=payload.title,
        answer_type=payload.answer_type,
        scale_min=payload.scale_min,
        scale_max=payload.scale_max,
        options=json.dumps(payload.options) if payload.options is not None else None,
        schedule=json.dumps(payload.schedule.model_dump()),
        due_window_minutes=payload.due_window_minutes,
        reminder_minutes=payload.reminder_minutes,
        active=True,
        created_at=now,
        updated_at=now,
    )
    async with db.session_factory() as session:
        session.add(row)
        await session.commit()
        await session.refresh(row)
        return _definition_out(row, ZERO_COUNTS)


async def get_definition(db: Database, definition_id: str) -> dict:
    async with db.session_factory() as session:
        row = await session.get(CheckinDefinition, definition_id)
        if row is None:
            raise HTTPException(status_code=404, detail="check-in not found")
        return _definition_out(row, ZERO_COUNTS)


async def update_definition(db: Database, definition_id: str, payload: CheckinWrite) -> dict:
    async with db.session_factory() as session:
        row = await session.get(CheckinDefinition, definition_id)
        if row is None:
            raise HTTPException(status_code=404, detail="check-in not found")
        row.title = payload.title
        row.answer_type = payload.answer_type
        row.scale_min = payload.scale_min
        row.scale_max = payload.scale_max
        row.options = json.dumps(payload.options) if payload.options is not None else None
        row.schedule = json.dumps(payload.schedule.model_dump())
        row.due_window_minutes = payload.due_window_minutes
        row.reminder_minutes = payload.reminder_minutes
        row.updated_at = utc_now_iso()
        await session.commit()
        await session.refresh(row)
        return _definition_out(row, ZERO_COUNTS)


async def set_active(db: Database, definition_id: str, active: bool) -> dict:
    async with db.session_factory() as session:
        row = await session.get(CheckinDefinition, definition_id)
        if row is None:
            raise HTTPException(status_code=404, detail="check-in not found")
        row.active = active
        row.updated_at = utc_now_iso()
        await session.commit()
        await session.refresh(row)
        return _definition_out(row, ZERO_COUNTS)


async def delete_definition(db: Database, definition_id: str) -> None:
    """Hard delete only when there are no instances (data-preserving rule)."""
    async with db.session_factory() as session:
        row = await session.get(CheckinDefinition, definition_id)
        if row is None:
            raise HTTPException(status_code=404, detail="check-in not found")
        (count,) = (
            await session.execute(
                select(func.count())
                .select_from(CheckinInstance)
                .where(CheckinInstance.definition_id == definition_id)
            )
        ).one()
        if count:
            raise HTTPException(
                status_code=409,
                detail="cannot delete a check-in with scheduled instances; deactivate it instead",
            )
        await session.delete(row)
        await session.commit()


# --- Reads -----------------------------------------------------------------


async def list_definitions(db: Database) -> list[dict]:
    async with db.session_factory() as session:
        rows = (
            (
                await session.execute(
                    select(CheckinDefinition).order_by(CheckinDefinition.created_at)
                )
            )
            .scalars()
            .all()
        )
        counts = await _instance_counts(session, None)
        return [_definition_out(r, counts.get(r.id, ZERO_COUNTS)) for r in rows]


async def list_instances(
    db: Database,
    definition_id: str | None = None,
    status: str | None = None,
    from_local: str | None = None,
    to_local: str | None = None,
    limit: int = 200,
) -> list[dict]:
    async with db.session_factory() as session:
        stmt = select(CheckinInstance, CheckinDefinition.title).join(
            CheckinDefinition, CheckinInstance.definition_id == CheckinDefinition.id
        )
        if definition_id:
            stmt = stmt.where(CheckinInstance.definition_id == definition_id)
        if status:
            stmt = stmt.where(CheckinInstance.status == status)
        if from_local:
            stmt = stmt.where(CheckinInstance.scheduled_local >= from_local)
        if to_local:
            stmt = stmt.where(CheckinInstance.scheduled_local <= to_local)
        stmt = stmt.order_by(CheckinInstance.scheduled_utc).limit(min(limit, 500))
        return [_instance_out(inst, title) for inst, title in (await session.execute(stmt)).all()]


async def list_responses(
    db: Database,
    definition_id: str | None = None,
    from_utc: str | None = None,
    to_utc: str | None = None,
) -> list[dict]:
    async with db.session_factory() as session:
        stmt = (
            select(CheckinResponse, CheckinInstance, CheckinDefinition)
            .join(CheckinInstance, CheckinResponse.instance_id == CheckinInstance.id)
            .join(CheckinDefinition, CheckinInstance.definition_id == CheckinDefinition.id)
        )
        if definition_id:
            stmt = stmt.where(CheckinInstance.definition_id == definition_id)
        if from_utc:
            stmt = stmt.where(CheckinResponse.submitted_at >= from_utc)
        if to_utc:
            stmt = stmt.where(CheckinResponse.submitted_at <= to_utc)
        stmt = stmt.order_by(CheckinResponse.submitted_at.desc()).limit(1000)
        return [
            {
                "id": resp.id,
                "instance_id": resp.instance_id,
                "title": defn.title,
                "answer_type": defn.answer_type,
                "value": _value_out(resp, defn.answer_type),
                "scheduled_local": inst.scheduled_local,
                "submitted_at": resp.submitted_at,
            }
            for resp, inst, defn in (await session.execute(stmt)).all()
        ]


# --- Device endpoints ------------------------------------------------------


async def list_pending(db: Database) -> list[dict]:
    async with db.session_factory() as session:
        stmt = (
            select(CheckinInstance, CheckinDefinition)
            .join(CheckinDefinition, CheckinInstance.definition_id == CheckinDefinition.id)
            .where(CheckinInstance.status == STATUS_PENDING)
            .order_by(CheckinInstance.scheduled_utc)
        )
        out = []
        for inst, defn in (await session.execute(stmt)).all():
            due = from_utc_iso(inst.scheduled_utc) + timedelta(minutes=defn.due_window_minutes)
            out.append(
                {
                    "instance_id": inst.id,
                    "question": defn.title,
                    "answer_type": defn.answer_type,
                    "options": json.loads(defn.options) if defn.options else None,
                    "scale": (
                        {"min": defn.scale_min, "max": defn.scale_max}
                        if defn.answer_type == "scale"
                        else None
                    ),
                    "scheduled_at": inst.scheduled_utc,
                    "due_by": due.isoformat().replace("+00:00", "Z"),
                }
            )
        return out


async def submit_response(db: Database, instance_id: str, payload: AnswerPayload) -> dict:
    """Submit an answer. Semantics: 404 unknown, 409 conflicting duplicate,
    410 after the window, 200 idempotent for identical repeats."""
    async with db.session_factory() as session:
        inst = await session.get(CheckinInstance, instance_id)
        if inst is None:
            raise HTTPException(status_code=404, detail="check-in instance not found")
        defn = await session.get(CheckinDefinition, inst.definition_id)

        if inst.status == STATUS_ANSWERED:
            existing = (
                await session.execute(
                    select(CheckinResponse).where(CheckinResponse.instance_id == instance_id)
                )
            ).scalar_one()
            if _value_same(existing, defn, payload):
                return _response_out(existing, defn)
            raise HTTPException(status_code=409, detail="instance already answered")

        if inst.status == STATUS_MISSED:
            raise HTTPException(status_code=410, detail="answer window has closed; instance missed")

        _validate_answer(defn, payload)
        now = utc_now_iso()
        response = CheckinResponse(
            instance_id=instance_id,
            value_boolean=int(payload.boolean) if payload.boolean is not None else None,
            value_scale=payload.scale,
            value_choice=payload.choice,
            submitted_at=now,
        )
        inst.status = STATUS_ANSWERED
        inst.answered_at = now
        session.add(response)
        await session.commit()
        await session.refresh(response)
        return _response_out(response, defn)


# --- The tick (scheduler task) --------------------------------------------


async def tick(settings: Settings, db: Database, now: datetime | None = None) -> None:
    """One check-in tick: materialise due instances, mark missed, send notifications.
    ``now`` defaults to the current time and is injectable for tests."""
    now = now or datetime.now(UTC)
    new_instances = await _materialise_instances(settings, db, now)
    await _mark_missed(settings, db, now)
    await _process_reminders(settings, db, now)
    await _notify_new(settings, db, new_instances)


async def _materialise_instances(settings: Settings, db: Database, now: datetime) -> list[str]:
    """Create due instances for active definitions in (now - lookback, now].

    Skips occurrences already past their answer window (they would be expired on
    arrival). Returns the ids of freshly created instances.
    """
    tz = settings.timezone
    now_local = now.astimezone(tz).replace(tzinfo=None)
    start_local = now_local - timedelta(days=LOOKBACK_DAYS)

    created: list[str] = []
    async with db.session_factory() as session:
        definitions = (
            (
                await session.execute(
                    select(CheckinDefinition).where(CheckinDefinition.active.is_(True))
                )
            )
            .scalars()
            .all()
        )

        existing_rows = (
            await session.execute(
                select(CheckinInstance.definition_id, CheckinInstance.scheduled_utc)
            )
        ).all()
        existing = {(d, s) for d, s in existing_rows}

        for defn in definitions:
            try:
                schedule = Schedule.model_validate(json.loads(defn.schedule))
            except Exception:  # noqa: BLE001 - a bad schedule must not break the tick
                logger.warning("check-in %s has an invalid schedule; skipping", defn.id)
                continue

            for local_dt in occurrences(schedule, start_local, now_local):
                sched_utc = to_utc_iso(local_dt, tz)
                if (defn.id, sched_utc) in existing:
                    continue
                due = from_utc_iso(sched_utc) + timedelta(minutes=defn.due_window_minutes)
                if due < now:  # expired on arrival (e.g. after downtime) — skip
                    existing.add((defn.id, sched_utc))
                    continue
                instance = CheckinInstance(
                    definition_id=defn.id,
                    scheduled_local=local_dt.isoformat(timespec="seconds"),
                    scheduled_utc=sched_utc,
                    status=STATUS_PENDING,
                )
                session.add(instance)
                await session.flush()  # assign the id so we can notify for it
                existing.add((defn.id, sched_utc))
                created.append(instance.id)

            if schedule.repeat == REPEAT_ONCE:
                once = datetime.strptime(schedule.scheduled_at or "", "%Y-%m-%dT%H:%M")
                once_utc = to_utc_iso(once, tz)
                if from_utc_iso(once_utc) < now:
                    defn.active = False
                    defn.updated_at = utc_now_iso()

        await session.commit()
    return created


async def _mark_missed(settings: Settings, db: Database, now: datetime) -> None:
    now_iso = now.isoformat().replace("+00:00", "Z")
    async with db.session_factory() as session:
        rows = (
            (
                await session.execute(
                    select(CheckinInstance).where(CheckinInstance.status == STATUS_PENDING)
                )
            )
            .scalars()
            .all()
        )
        for inst in rows:
            # due window comes from the *current* definition (the defn may have changed)
            defn = await session.get(CheckinDefinition, inst.definition_id)
            due = from_utc_iso(inst.scheduled_utc) + timedelta(minutes=defn.due_window_minutes)
            if due <= now:
                inst.status = STATUS_MISSED
                inst.missed_at = now_iso
        await session.commit()


async def _process_reminders(settings: Settings, db: Database, now: datetime) -> None:
    if not pushover.enabled(settings):
        return
    now_iso = now.isoformat().replace("+00:00", "Z")
    async with db.session_factory() as session:
        rows = (
            (
                await session.execute(
                    select(CheckinInstance).where(CheckinInstance.status == STATUS_PENDING)
                )
            )
            .scalars()
            .all()
        )
        for inst in rows:
            defn = await session.get(CheckinDefinition, inst.definition_id)
            if not defn.reminder_minutes or inst.reminder_sent_at:
                continue
            cutoff = from_utc_iso(inst.scheduled_utc) + timedelta(minutes=defn.reminder_minutes)
            if cutoff <= now:
                try:
                    await pushover.send(settings)
                    inst.reminder_sent_at = now_iso
                except Exception as exc:  # noqa: BLE001 - never fail the tick
                    logger.warning("pushover reminder failed: %s", exc)
        await session.commit()


async def _notify_new(settings: Settings, db: Database, instance_ids: list[str]) -> None:
    if not pushover.enabled(settings) or not instance_ids:
        return
    now_iso = utc_now_iso()
    async with db.session_factory() as session:
        for instance_id in instance_ids:
            inst = await session.get(CheckinInstance, instance_id)
            if inst is None or inst.pushover_sent_at:
                continue
            try:
                await pushover.send(settings)
                inst.pushover_sent_at = now_iso
            except Exception as exc:  # noqa: BLE001 - pushover failure is non-fatal
                logger.warning("pushover notification failed: %s", exc)
        await session.commit()


# --- Rendering helpers -----------------------------------------------------


ZERO_COUNTS = {"pending": 0, "answered": 0, "missed": 0}


def _definition_out(row: CheckinDefinition, counts: dict) -> dict:
    return {
        "id": row.id,
        "title": row.title,
        "answer_type": row.answer_type,
        "scale_min": row.scale_min,
        "scale_max": row.scale_max,
        "options": json.loads(row.options) if row.options else None,
        "schedule": json.loads(row.schedule),
        "due_window_minutes": row.due_window_minutes,
        "reminder_minutes": row.reminder_minutes,
        "active": row.active,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
        "instance_counts": counts,
    }


def _instance_out(inst: CheckinInstance, title: str) -> dict:
    return {
        "id": inst.id,
        "title": title,
        "scheduled_local": inst.scheduled_local,
        "status": inst.status,
        "answered_at": inst.answered_at,
        "missed_at": inst.missed_at,
    }


def _value_out(resp: CheckinResponse, answer_type: str) -> dict:
    if answer_type == "boolean":
        return {"boolean": bool(resp.value_boolean)}
    if answer_type == "scale":
        return {"scale": resp.value_scale}
    return {"choice": resp.value_choice}


def _value_same(resp: CheckinResponse, defn: CheckinDefinition, payload: AnswerPayload) -> bool:
    if defn.answer_type == "boolean":
        return resp.value_boolean == int(bool(payload.boolean))
    if defn.answer_type == "scale":
        return resp.value_scale == payload.scale
    return resp.value_choice == payload.choice


def _validate_answer(defn: CheckinDefinition, payload: AnswerPayload) -> None:
    if defn.answer_type == "boolean":
        if payload.boolean is None:
            raise HTTPException(422, "expected a boolean answer")
    elif defn.answer_type == "scale":
        if payload.scale is None or not (defn.scale_min <= payload.scale <= defn.scale_max):
            raise HTTPException(
                422, f"expected a scale answer between {defn.scale_min} and {defn.scale_max}"
            )
    else:  # single_choice
        options = json.loads(defn.options) if defn.options else []
        if payload.choice is None or payload.choice not in options:
            raise HTTPException(422, "choice must be one of the defined options")


def _response_out(resp: CheckinResponse, defn: CheckinDefinition) -> dict:
    return {
        "id": resp.id,
        "instance_id": resp.instance_id,
        "value": _value_out(resp, defn.answer_type),
        "submitted_at": resp.submitted_at,
    }


async def _instance_counts(session: AsyncSession, definition_id: str | None) -> dict:
    stmt = select(CheckinInstance.definition_id, CheckinInstance.status, func.count()).group_by(
        CheckinInstance.definition_id, CheckinInstance.status
    )
    if definition_id:
        stmt = stmt.where(CheckinInstance.definition_id == definition_id)
    counts: dict[str, dict] = {}
    for def_id, status, count in (await session.execute(stmt)).all():
        counts.setdefault(def_id, dict(ZERO_COUNTS))[status] = count
    return counts
