"""Async SQLite engine and session factory with the required PRAGMAs."""

from __future__ import annotations

from collections.abc import AsyncIterator

from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from guai_hub.config import Settings


def make_engine(db_path: str):
    """Create an async SQLite engine for ``db_path`` (a file path or ``:memory:``)."""
    return create_async_engine(
        f"sqlite+aiosqlite:///{db_path}",
        connect_args={"check_same_thread": False},
    )


def _listen_for_sqlite_pragmas(engine) -> None:
    """Enable WAL, foreign keys, busytimeout and sync pragmas on every connection."""

    def _set_pragmas(dbapi_connection, connection_record) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout=5000")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.close()

    event.listen(engine.sync_engine, "connect", _set_pragmas)


def make_session_factory(engine):
    return async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


class Database:
    """Owns the engine and session factory for one database file."""

    def __init__(self, db_path: str) -> None:
        self.db_path = db_path
        self.engine = make_engine(db_path)
        _listen_for_sqlite_pragmas(self.engine)
        self.session_factory = make_session_factory(self.engine)

    async def dispose(self) -> None:
        await self.engine.dispose()

    async def session(self) -> AsyncIterator[AsyncSession]:
        async with self.session_factory() as session:
            yield session

    async def ping(self) -> None:
        """Raise if the database connection is not usable (used by /healthz)."""
        from sqlalchemy import text

        async with self.engine.connect() as conn:
            await conn.execute(text("SELECT 1"))


def database_from_settings(settings: Settings) -> Database:
    return Database(settings.db_path)
