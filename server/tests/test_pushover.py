"""Pushover behaviour tests (Step 3 gate): generic message, disabled mode,
failure tolerance, and the single reminder."""

from datetime import UTC, datetime

import httpx
from sqlalchemy import text

from guai_hub.config import Settings
from guai_hub.database import Database
from guai_hub.schemas import CheckinWrite
from guai_hub.services import checkins

TOKEN = "0123456789abcdef"


def _settings(db_path, **extra):
    kwargs = {"tz": "UTC", "sync_token": TOKEN, "db_path": db_path, "enable_scheduler": False}
    kwargs.update(extra)
    return Settings(**kwargs)


def _def(answer_type="boolean", **overrides):
    payload = {
        "title": "How are you feeling today?",
        "answer_type": answer_type,
        "schedule": {"repeat": "daily", "times": ["09:00"]},
        "due_window_minutes": 60,
        "reminder_minutes": 0,
    }
    payload.update(overrides)
    return CheckinWrite.model_validate(payload)


class FakeResponse:
    def __init__(self, status=200):
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("boom", request=None, response=self)


class FakeClient:
    def __init__(self, responses):
        self.responses = list(responses)
        self.posts = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def aclose(self):
        return None

    async def post(self, url, data=None, **kwargs):
        self.posts.append(data or {})
        return self.responses.pop(0)


def _patch_client(monkeypatch, responses):
    from guai_hub.services import pushover

    fake = FakeClient(responses)
    monkeypatch.setattr(pushover.httpx, "AsyncClient", lambda *a, **kw: fake)
    return fake


async def _field(db, sql):
    async with db.session_factory() as s:
        return (await s.execute(text(sql))).scalar_one()


async def test_due_notification_is_generic_and_records_sent(monkeypatch, migrated_db_path):
    fake = _patch_client(monkeypatch, [FakeResponse()])
    settings = _settings(migrated_db_path, pushover_token="pt", pushover_user="pu")
    db = Database(migrated_db_path)
    try:
        await checkins.create_definition(db, _def())
        await checkins.tick(settings, db, datetime(2026, 10, 19, 9, 0, tzinfo=UTC))

        assert len(fake.posts) == 1
        body = fake.posts[0]
        assert body["title"] == "GUAI"
        assert "question" not in body["message"].lower()
        assert "waiting in the GUAI app" in body["message"]
        assert body["token"] == "pt" and body["user"] == "pu"

        sent = await _field(db, "SELECT pushover_sent_at FROM checkin_instances LIMIT 1")
        assert sent is not None
    finally:
        await db.dispose()


async def test_disabled_no_notifications(migrated_db_path):
    settings = _settings(migrated_db_path)  # no pushover creds
    db = Database(migrated_db_path)
    try:
        await checkins.create_definition(db, _def())
        await checkins.tick(settings, db, datetime(2026, 10, 19, 9, 0, tzinfo=UTC))
        sent = await _field(db, "SELECT pushover_sent_at FROM checkin_instances LIMIT 1")
        assert sent is None
    finally:
        await db.dispose()


async def test_pushover_failure_does_not_break_tick(monkeypatch, migrated_db_path):
    _patch_client(monkeypatch, [FakeResponse(500)])
    settings = _settings(migrated_db_path, pushover_token="pt", pushover_user="pu")
    db = Database(migrated_db_path)
    try:
        await checkins.create_definition(db, _def())
        await checkins.tick(settings, db, datetime(2026, 10, 19, 9, 0, tzinfo=UTC))
        # instance still created despite the failed push
        count = await _field(db, "SELECT COUNT(*) FROM checkin_instances")
        assert count == 1
    finally:
        await db.dispose()


async def test_reminder_sent_exactly_once(monkeypatch, migrated_db_path):
    fake = _patch_client(monkeypatch, [FakeResponse(), FakeResponse()])
    settings = _settings(migrated_db_path, pushover_token="pt", pushover_user="pu")
    db = Database(migrated_db_path)
    try:
        await checkins.create_definition(db, _def(reminder_minutes=10))
        t9 = datetime(2026, 10, 19, 9, 0, tzinfo=UTC)
        await checkins.tick(settings, db, t9)  # due notification
        await checkins.tick(settings, db, datetime(2026, 10, 19, 9, 11, tzinfo=UTC))  # reminder
        await checkins.tick(settings, db, datetime(2026, 10, 19, 9, 30, tzinfo=UTC))  # no-op

        assert len(fake.posts) == 2  # due + one reminder, never more
        reminder = await _field(db, "SELECT reminder_sent_at FROM checkin_instances LIMIT 1")
        assert reminder is not None
    finally:
        await db.dispose()
