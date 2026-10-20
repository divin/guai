"""Server-mode composition (``GUAI_SERVER_MODE``): one codebase, three app shapes.

- ``full`` (default): device + internal endpoints + frontend — today's single instance.
- ``device``: only the token-protected endpoints the watchface/app need; no dashboard
  routes, no frontend. This is the instance exposed to the internet.
- ``internal``: dashboard + management APIs + frontend; no device endpoints. LAN only.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from guai_hub.config import Settings
from guai_hub.main import create_app

TOKEN = "0123456789abcdef"

# Routes that must exist on the device instance (route-presence probe: an
# existing route answers 401 without a token; a missing route answers 404).
DEVICE_ROUTES = [
    ("POST", "/api/sync"),
    ("GET", "/api/checkins/pending"),
    ("POST", "/api/checkins/instances/x/responses"),
    ("GET", "/api/weather/current"),
]

# Dashboard/management routes that must never be exposed on the device instance.
INTERNAL_ROUTES = [
    ("GET", "/api/checkins"),
    ("POST", "/api/checkins"),
    ("GET", "/api/checkin-instances"),
    ("GET", "/api/checkin-responses"),
    ("GET", "/api/weather"),
    ("GET", "/api/weather/latest"),
    ("GET", "/api/daily"),
    ("GET", "/api/stats"),
    ("GET", "/api/export.csv"),
    ("GET", "/api/config"),
]


def _client(monkeypatch, tmp_path, mode: str) -> TestClient:
    monkeypatch.setenv("TZ", "UTC")
    monkeypatch.setenv("GUAI_SYNC_TOKEN", TOKEN)
    monkeypatch.setenv("GUAI_DB_PATH", str(tmp_path / "guai.sqlite3"))
    monkeypatch.setenv("GUAI_SCHEDULER", "0")
    monkeypatch.setenv("GUAI_SERVER_MODE", mode)
    app = create_app(Settings())
    return TestClient(app)


def _probe(client: TestClient, method: str, path: str) -> int:
    return client.request(method, path).status_code


def test_device_mode_serves_only_device_surface(monkeypatch, tmp_path):
    with _client(monkeypatch, tmp_path, "device") as client:
        assert client.get("/healthz").status_code == 200
        for method, path in DEVICE_ROUTES:
            assert _probe(client, method, path) == 401, f"{method} {path} missing"
        for method, path in INTERNAL_ROUTES:
            assert _probe(client, method, path) == 404, f"{method} {path} exposed"
        # no frontend on the exposed instance
        assert client.get("/").status_code == 404


def test_internal_mode_serves_only_internal_surface(monkeypatch, tmp_path):
    with _client(monkeypatch, tmp_path, "internal") as client:
        assert client.get("/healthz").status_code == 200
        for method, path in INTERNAL_ROUTES:
            assert _probe(client, method, path) != 404, f"{method} {path} missing"
        for method, path in DEVICE_ROUTES:
            # 404/405 both mean "not served" (the static mount answers 405 for
            # unknown POSTs when the frontend is built); nothing must answer 401.
            assert _probe(client, method, path) not in (200, 201, 401), f"{method} {path} exposed"


def test_full_mode_keeps_both_surfaces(monkeypatch, tmp_path):
    with _client(monkeypatch, tmp_path, "full") as client:
        assert client.get("/healthz").status_code == 200
        for method, path in DEVICE_ROUTES:
            assert _probe(client, method, path) == 401, f"{method} {path} missing"
        for method, path in INTERNAL_ROUTES:
            assert _probe(client, method, path) != 404, f"{method} {path} missing"


def test_device_mode_rejects_bad_token(monkeypatch, tmp_path):
    with _client(monkeypatch, tmp_path, "device") as client:
        assert (
            client.get(
                "/api/checkins/pending", headers={"Authorization": "Bearer wrong"}
            ).status_code
            == 401
        )
        assert (
            client.get(
                "/api/checkins/pending", headers={"Authorization": f"Bearer {TOKEN}"}
            ).status_code
            == 200
        )


def test_invalid_mode_is_rejected(monkeypatch, tmp_path):
    from pydantic import ValidationError

    monkeypatch.setenv("TZ", "UTC")
    monkeypatch.setenv("GUAI_SYNC_TOKEN", TOKEN)
    monkeypatch.setenv("GUAI_DB_PATH", str(tmp_path / "guai.sqlite3"))
    monkeypatch.setenv("GUAI_SERVER_MODE", "yolo")
    with pytest.raises(ValidationError):
        Settings()


def test_device_mode_skips_root_fallback(monkeypatch, tmp_path):
    # Without a built frontend, full/internal answer / with a JSON hint; the exposed
    # device instance has no root route at all.
    with _client(monkeypatch, tmp_path, "full") as client:
        assert client.get("/").status_code == 200
    with _client(monkeypatch, tmp_path, "device") as client:
        assert client.get("/").status_code == 404
