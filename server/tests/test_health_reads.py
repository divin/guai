"""Health read endpoints (Step 5 dependency): GET /api/activity|sleep|heart-rate."""

TOKEN = "0123456789abcdef"


def auth():
    return {"Authorization": f"Bearer {TOKEN}"}


def _payload():
    return {
        "collected_at": "2026-09-19T09:00:00Z",
        "activity": {"steps": 8124, "active_seconds": 2210},
        "heart_rates": [{"at": "2026-09-19T09:00:00Z", "bpm": 62}],
        "sleep": [{"night_date": "2026-09-19", "sleep_seconds": 25200}],
    }


def test_read_endpoints_roundtrip(client):
    assert client.post("/api/sync", json=_payload(), headers=auth()).status_code == 200

    activity = client.get("/api/activity").json()
    assert len(activity) == 1 and activity[0]["steps"] == 8124

    hr = client.get("/api/heart-rate").json()
    assert len(hr) == 1 and hr[0]["bpm"] == 62

    sleep = client.get("/api/sleep").json()
    assert len(sleep) == 1 and sleep[0]["sleep_seconds"] == 25200


def test_read_endpoints_respect_range(client):
    client.post("/api/sync", json=_payload(), headers=auth())
    # a range that excludes the data
    assert (
        client.get("/api/activity", params={"from": "2026-10-01", "to": "2026-10-02"}).json() == []
    )
    # a range that includes it
    rows = client.get("/api/activity", params={"from": "2026-09-01", "to": "2026-09-30"}).json()
    assert len(rows) == 1

    assert client.get("/api/sleep", params={"from": "2026-10-01"}).json() == []
    assert client.get("/api/heart-rate", params={"to": "2026-09-30"}).json()[0]["bpm"] == 62


def test_read_endpoints_cap_keeps_newest_rows(client):
    for hour in range(5):
        payload = dict(_payload())
        payload["collected_at"] = f"2026-09-19T{hour:02d}:00:00Z"
        payload["heart_rates"] = [{"at": f"2026-09-19T{hour:02d}:00:00Z", "bpm": 60 + hour}]
        client.post("/api/sync", json=payload, headers=auth())

    rows = client.get("/api/activity", params={"limit": 2}).json()
    assert [r["event_time"] for r in rows] == ["2026-09-19T03:00:00Z", "2026-09-19T04:00:00Z"]

    hr = client.get("/api/heart-rate", params={"limit": 2}).json()
    assert [r["bpm"] for r in hr] == [63, 64]  # newest two, still oldest-first


def test_read_endpoints_have_no_auth_and_are_ordered(client):
    client.post("/api/sync", json=_payload(), headers=auth())
    second = dict(_payload())
    second["collected_at"] = "2026-09-19T10:00:00Z"
    second["heart_rates"] = [{"at": "2026-09-19T10:00:00Z", "bpm": 70}]
    client.post("/api/sync", json=second, headers=auth())

    rows = client.get("/api/heart-rate").json()  # no token required
    assert [r["bpm"] for r in rows] == [62, 70]  # oldest first
