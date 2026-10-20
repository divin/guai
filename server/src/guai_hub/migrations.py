"""Alembic helpers: run ``alembic upgrade head`` (idempotent) at startup.

The migration scripts live at ``server/migrations`` in the repo and are copied to
``/app/migrations`` in the container (``GUAI_MIGRATIONS_DIR``). For local
development, the repo location is derived from this file.
"""

from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config

from guai_hub.config import get_settings

DEFAULT_MIGRATIONS_DIR = (
    Path(__file__).resolve().parent.parent.parent / "migrations"
)  # .../server/migrations


def alembic_config(db_path: str, migrations_dir: str | None = None) -> Config:
    mdir = Path(migrations_dir) if migrations_dir else DEFAULT_MIGRATIONS_DIR
    # No ini file needed: script_location and URL are set explicitly. env.py skips
    # fileConfig when config_file_name is None.
    cfg = Config()
    cfg.set_main_option("script_location", str(mdir))
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db_path}")
    return cfg


def run_migrations(db_path: str | None = None, migrations_dir: str | None = None) -> None:
    """Apply all pending migrations up to head. No-op at head (idempotent)."""
    settings = get_settings()
    path = db_path if db_path is not None else settings.db_path
    mdir = migrations_dir or settings.migrations_dir or None
    command.upgrade(alembic_config(path, mdir), "head")
