"""FastAPI application factory, lifespan (migrate -> scheduler -> serve) and middlewares.

The factory composes the app by ``GUAI_SERVER_MODE``: ``full`` (default — everything in
one instance), ``device`` (only the token-protected endpoints for the watchface/app —
the exposed instance), or ``internal`` (dashboard + management APIs, frontend, scheduler,
migrations — LAN only). See ``spec/roadmap.md`` step 12.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from guai_hub.config import Settings, get_settings
from guai_hub.constants import MAX_BODY_BYTES
from guai_hub.database import Database
from guai_hub.migrations import run_migrations
from guai_hub.routers import checkins as checkins_routes
from guai_hub.routers import config as config_routes
from guai_hub.routers import device as device_routes
from guai_hub.routers import health as health_routes
from guai_hub.routers import insights as insights_routes
from guai_hub.routers import weather as weather_routes
from guai_hub.scheduler import BackgroundJobs


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build the application. ``settings`` defaults to the process environment."""
    settings = settings or get_settings()
    db = Database(settings.db_path)
    # Single-writer discipline: the exposed device instance runs no scheduler even
    # if misconfigured — the tick, weather fetch, and Pushover belong to the
    # primary (full/internal) instance only.
    primary = settings.server_mode != "device"
    jobs = BackgroundJobs(settings, db) if settings.enable_scheduler and primary else None

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # Only the primary instance migrates; two instances share the SQLite file,
        # and racing Alembic on startup is exactly what this avoids. Migrations are
        # idempotent at head, so the primary re-checks cheaply on every start.
        if primary:
            run_migrations(settings.db_path)
        await db.ping()
        if jobs is not None:
            jobs.start()
        try:
            yield
        finally:
            if jobs is not None:
                await jobs.shutdown()
            await db.dispose()

    app = FastAPI(title="GUAI", lifespan=lifespan)
    app.state.settings = settings
    app.state.db = db

    # Reject oversized request bodies before parsing (1 MiB cap).
    @app.middleware("http")
    async def limit_body_size(request: Request, call_next):
        if request.method in ("POST", "PUT", "PATCH"):
            length = request.headers.get("content-length")
            if length and length.isdigit() and int(length) > MAX_BODY_BYTES:
                return JSONResponse({"detail": "request body too large"}, status_code=413)
        return await call_next(request)

    @app.get("/healthz")
    async def healthz(request: Request) -> JSONResponse:
        try:
            await request.app.state.db.ping()
        except Exception:  # noqa: BLE001 - any DB failure means unhealthy
            return JSONResponse({"status": "error"}, status_code=503)
        return JSONResponse({"status": "ok"})

    # API routers, composed by server mode. Only the device endpoints are
    # token-protected; everything else is trusted-LAN.
    if settings.server_mode in ("full", "device"):
        app.include_router(device_routes.router, prefix="/api")
    if settings.server_mode in ("full", "internal"):
        app.include_router(config_routes.router, prefix="/api")
        app.include_router(weather_routes.router, prefix="/api")
        app.include_router(health_routes.router, prefix="/api")
        app.include_router(insights_routes.router, prefix="/api")
        app.include_router(checkins_routes.router, prefix="/api")

    # Zola static site — internal/full instances only; mounted last so /healthz
    # and /api routes win. The exposed device instance serves no frontend.
    if settings.server_mode != "device":
        static_dir = Path(settings.static_dir)
        if static_dir.is_dir():
            app.mount("/", StaticFiles(directory=str(static_dir), html=True), name="static")
        else:

            @app.get("/")
            async def root() -> JSONResponse:
                return JSONResponse(
                    {
                        "service": "guai-hub",
                        "note": "frontend not built; run `just frontend-build`",
                    },
                    status_code=200,
                )

    return app


app = create_app
