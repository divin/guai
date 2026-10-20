"""/healthz endpoint tests (Step 1 gate). DB-only; 200 when up, 503 when down."""

from unittest.mock import AsyncMock

from fastapi.testclient import TestClient

from guai_hub.main import create_app


def test_healthz_ok(settings):
    app = create_app(settings)
    with TestClient(app) as client:
        resp = client.get("/healthz")
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok"}


def test_healthz_503_when_database_down(settings):
    app = create_app(settings)
    with TestClient(app) as client:
        app.state.db.ping = AsyncMock(side_effect=Exception("db unavailable"))
        resp = client.get("/healthz")
        assert resp.status_code == 503
        assert resp.json() == {"status": "error"}


def test_root_is_served(settings):
    app = create_app(settings)
    with TestClient(app) as client:
        resp = client.get("/")
        # Served either by the mounted static site (200 html) or the build-needed fallback.
        assert resp.status_code == 200
