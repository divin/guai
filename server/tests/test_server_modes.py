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
    # production order: the primary migrates before the device instance serves
    from guai_hub.migrations import run_migrations

    run_migrations(str(tmp_path / "guai.sqlite3"))
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


# --- 12.2: single-writer discipline (migrations + scheduler in the primary only) ---


_spy_started: list = []
_spy_stopped: list = []


class _SpyJobs:
    """Stands in for BackgroundJobs so tests never start real timers."""

    def __init__(self, settings, db):
        _spy_started.append(self)

    def start(self):
        pass

    async def shutdown(self):
        _spy_stopped.append(self)


def _watch_lifespan(monkeypatch):
    """Record run_migrations calls; swap BackgroundJobs for the spy."""
    import guai_hub.main as main_mod

    real = main_mod.run_migrations
    calls = []

    def spy(path):
        calls.append(path)
        return real(path)  # wrap, don't replace — the DB must still be migrated

    monkeypatch.setattr(main_mod, "run_migrations", spy)
    monkeypatch.setattr(main_mod, "BackgroundJobs", _SpyJobs)
    _spy_started.clear()
    _spy_stopped.clear()
    return calls


def _mode_env(monkeypatch, tmp_path, mode, scheduler="0"):
    monkeypatch.setenv("TZ", "UTC")
    monkeypatch.setenv("GUAI_SYNC_TOKEN", TOKEN)
    monkeypatch.setenv("GUAI_DB_PATH", str(tmp_path / "guai.sqlite3"))
    monkeypatch.setenv("GUAI_SCHEDULER", scheduler)
    monkeypatch.setenv("GUAI_SERVER_MODE", mode)
    return Settings()


def test_device_mode_never_migrates_or_schedules(monkeypatch, tmp_path):
    calls = _watch_lifespan(monkeypatch)
    monkeypatch.setenv("TZ", "UTC")
    monkeypatch.setenv("GUAI_SYNC_TOKEN", TOKEN)
    monkeypatch.setenv("GUAI_DB_PATH", str(tmp_path / "guai.sqlite3"))
    monkeypatch.setenv("GUAI_SCHEDULER", "1")  # even misconfigured: no scheduler
    monkeypatch.setenv("GUAI_SERVER_MODE", "device")
    from fastapi.testclient import TestClient

    with TestClient(create_app(Settings())):
        pass
    assert calls == []  # no migrations
    assert _spy_started == []  # no scheduler


def test_primary_mode_migrates_and_schedules(monkeypatch, tmp_path):
    calls = _watch_lifespan(monkeypatch)
    monkeypatch.setenv("TZ", "UTC")
    monkeypatch.setenv("GUAI_SYNC_TOKEN", TOKEN)
    monkeypatch.setenv("GUAI_DB_PATH", str(tmp_path / "guai.sqlite3"))
    monkeypatch.setenv("GUAI_SCHEDULER", "1")
    monkeypatch.setenv("GUAI_SERVER_MODE", "internal")
    from fastapi.testclient import TestClient

    with TestClient(create_app(Settings())):
        pass
    assert len(calls) == 1  # migrations ran
    assert len(_spy_started) == 1  # scheduler started
    assert len(_spy_stopped) == 1  # and shut down cleanly


def test_two_instances_one_database(monkeypatch, tmp_path):
    """The deployment shape: primary (internal) + device app sharing one SQLite file."""
    from contextlib import ExitStack

    from fastapi.testclient import TestClient

    calls = _watch_lifespan(monkeypatch)
    shared = tmp_path / "shared.sqlite3"
    monkeypatch.setenv("TZ", "UTC")
    monkeypatch.setenv("GUAI_SYNC_TOKEN", TOKEN)
    monkeypatch.setenv("GUAI_DB_PATH", str(shared))
    monkeypatch.setenv("GUAI_SCHEDULER", "1")

    with ExitStack() as stack:
        # primary instance: internal mode (migrations + scheduler live here)
        monkeypatch.setenv("GUAI_SERVER_MODE", "internal")
        primary = stack.enter_context(TestClient(create_app(Settings())))
        # exposed instance: device mode
        monkeypatch.setenv("GUAI_SERVER_MODE", "device")
        device = stack.enter_context(TestClient(create_app(Settings())))

        # the app appears on the primary's dashboard APIs...
        assert primary.get("/api/checkins").status_code == 200
        # ...the device instance serves devices and shares the same DB state
        assert device.get("/healthz").status_code == 200

    # discipline held: migrations ran exactly once (primary), device ran none
    assert calls == [str(shared)]
