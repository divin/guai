"""FastAPI dependencies: bearer-token auth and an async DB session."""

from __future__ import annotations

import secrets
from collections.abc import AsyncIterator

from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

_bearer = HTTPBearer(auto_error=False)


async def get_db(request: Request) -> AsyncIterator[AsyncSession]:
    """Yield a session scoped to the request from the app's Database."""
    async with request.app.state.db.session_factory() as session:
        yield session


async def require_token(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> None:
    """Device endpoints require a valid ``Authorization: Bearer <GUAI_SYNC_TOKEN>``."""
    expected = request.app.state.settings.sync_token
    if credentials is None or not secrets.compare_digest(credentials.credentials, expected):
        raise HTTPException(status_code=401, detail="invalid or missing bearer token")
