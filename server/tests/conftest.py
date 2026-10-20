import pytest

from guai_hub.config import Settings

_ENV_KEYS = (
    "TZ",
    "GUAI_SYNC_TOKEN",
    "GUAI_DB_PATH",
    "GUAI_WEATHER_INTERVAL_MINUTES",
    "GUAI_STATIC_DIR",
    "PUSHOVER_TOKEN",
    "PUSHOVER_USER",
)


@pytest.fixture
def clean_env(monkeypatch):
    """Remove all GUAI env vars so tests are hermetic."""
    for key in _ENV_KEYS:
        monkeypatch.delenv(key, raising=False)


@pytest.fixture
def db_path(monkeypatch, tmp_path) -> str:
    path = str(tmp_path / "guai.sqlite3")
    monkeypatch.setenv("TZ", "UTC")
    monkeypatch.setenv("GUAI_SYNC_TOKEN", "0123456789abcdef")
    monkeypatch.setenv("GUAI_DB_PATH", path)
    return path


@pytest.fixture
def settings(monkeypatch, db_path) -> Settings:
    # Disable the background scheduler so TestClient-driven HTTP tests can't hang on it.
    monkeypatch.setenv("GUAI_SCHEDULER", "0")
    return Settings()


@pytest.fixture
def migrated_db_path(db_path) -> str:
    """A temp database path with schema v1 already applied."""
    from guai_hub.migrations import run_migrations

    run_migrations(db_path)
    return db_path


@pytest.fixture
def app(settings):
    from guai_hub.main import create_app

    return create_app(settings)


@pytest.fixture
def client(app):
    from fastapi.testclient import TestClient

    with TestClient(app) as c:
        yield c
