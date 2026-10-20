"""POST /api/sync endpoint tests (Step 2 gate): auth, validation, idempotent upserts."""

import sqlite3

TOKEN = "0123456789abcdef"


def auth() -> dict:
    return {"Authorization": f"Bearer {TOKEN}"}


def _synced_rows(settings, sql: str):
    con = sqlite3.connect(settings.db_path)
    try:
        return con.execute(sql).fetchall()
    finally:
        con.close()


def _payload(**overrides):
    base = {
        "collected_at": "2026-09-19T18:05:00Z",
        "device": "pebble-time",
        "activity": {
            "steps": 8124,
            "distance_meters": 5310.5,
            "active_seconds": 2210,
            "resting_kcal": 1480.2,
            "active_kcal": 320.4,
        },
        "heart_rates": [{"at": "2026-09-19T18:04:00Z", "bpm": 62}],
        "sleep": [{"night_date": "2026-09-19", "sleep_seconds": 25200, "restful_seconds": 18000}],
    }
    base.update(overrides)
    return base


def test_sync_requires_token(client):
    resp = client.post("/api/sync", json={})
    assert resp.status_code == 401


def test_sync_rejects_wrong_token(client):
    resp = client.post(
        "/api/sync", json={}, headers={"Authorization": "Bearer totally-wrong-token"}
    )
    assert resp.status_code == 401


def test_sync_empty_body_allowed_and_idempotent(client, settings):
    resp = client.post("/api/sync", json={}, headers=auth())
    assert resp.status_code == 200
    assert resp.json()["next_poll_seconds"] == 300
    assert "server_time" in resp.json()


def test_sync_happy_path_stores_all_sections(client, settings):
    resp = client.post("/api/sync", json=_payload(), headers=auth())
    assert resp.status_code == 200

    assert _synced_rows(settings, "SELECT steps, distance_meters FROM activity") == [(8124, 5310.5)]
    assert _synced_rows(settings, "SELECT bpm FROM heart_rate") == [(62,)]
    assert _synced_rows(settings, "SELECT sleep_seconds, restful_seconds FROM sleep") == [
        (25200, 18000)
    ]


def test_sync_duplicate_upload_is_idempotent(client, settings):
    headers = auth()
    client.post("/api/sync", json=_payload(), headers=headers)
    client.post("/api/sync", json=_payload(), headers=headers)

    assert len(_synced_rows(settings, "SELECT event_time FROM activity")) == 1
    assert len(_synced_rows(settings, "SELECT event_time FROM heart_rate")) == 1
    assert len(_synced_rows(settings, "SELECT night_date FROM sleep")) == 1


def test_sync_updates_existing_night_without_duplicate(client, settings):
    headers = auth()
    client.post("/api/sync", json=_payload(), headers=headers)
    updated = _payload()
    updated["sleep"][0]["sleep_seconds"] = 30000
    client.post("/api/sync", json=updated, headers=headers)

    assert _synced_rows(settings, "SELECT sleep_seconds, restful_seconds FROM sleep") == [
        (30000, 18000)
    ]
    assert len(_synced_rows(settings, "SELECT night_date FROM sleep")) == 1


def test_sync_activity_requires_collected_at(client):
    payload = _payload(activity={"steps": 1}, collected_at=None)
    assert client.post("/api/sync", json=payload, headers=auth()).status_code == 422


def test_sync_rejects_bad_heart_rate_bounds(client):
    payload = _payload()
    payload["heart_rates"] = [{"at": "2026-09-19T18:04:00Z", "bpm": 999}]
    assert client.post("/api/sync", json=payload, headers=auth()).status_code == 422


def test_sync_rejects_bad_timestamp(client):
    payload = _payload()
    payload["collected_at"] = "not-a-timestamp"
    assert client.post("/api/sync", json=payload, headers=auth()).status_code == 422


def test_sync_rejects_bad_night_date(client):
    payload = _payload()
    payload["sleep"] = [{"night_date": "2026-13-99"}]
    assert client.post("/api/sync", json=payload, headers=auth()).status_code == 422


def test_sync_rejects_oversized_body(client):
    payload = _payload()
    payload = {"pad": "x" * 1_300_000}
    resp = client.post("/api/sync", json=payload, headers=auth())
    assert resp.status_code == 413
