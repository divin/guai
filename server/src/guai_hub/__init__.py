"""GUAI server package.

The foundation lives in:

- ``config``: pydantic-settings configuration validated from the environment.
- ``models``: SQLModel table definitions (schema v1).
- ``database``: async SQLite engine/session factory.
- ``migrations``: Alembic helpers (``alembic upgrade head`` at startup).
- ``main``: the FastAPI application factory and the ``/healthz`` endpoint.
"""

from guai_hub.config import Settings, get_settings
from guai_hub.main import create_app

__all__ = ["Settings", "create_app", "get_settings"]
