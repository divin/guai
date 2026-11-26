"""Background jobs as asyncio tasks (run on the app's own loop).

Step 2: a periodic weather-poll loop. (Later milestones add the check-in tick the
same way: a one-minute task.) Async tasks avoid APScheduler's event-loop juggling
under uvicorn/uvloop and fit the single-worker design naturally.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta

from guai_hub.config import Settings
from guai_hub.database import Database
from guai_hub.forecasting.job import run_forecast_job
from guai_hub.services import checkins, weather

logger = logging.getLogger(__name__)

CHECKIN_TICK_SECONDS = 60


def _sanitize(exc: Exception) -> str:
    """Short, parameter-stripped exception summary for logs (never payload text)."""
    message = str(exc)
    cut = message.find("[parameters:")
    if cut != -1:
        message = message[:cut]
    return f"{type(exc).__name__}: {message[:160]}"


class BackgroundJobs:
    """Owns the long-lived background coroutines for the running app."""

    def __init__(self, settings: Settings, db: Database) -> None:
        self._settings = settings
        self._db = db
        self._tasks: list[asyncio.Task] = []

    def start(self) -> None:
        self._tasks.append(asyncio.create_task(self._weather_loop()))
        self._tasks.append(asyncio.create_task(self._checkin_loop()))
        self._tasks.append(asyncio.create_task(self._forecast_loop()))

    async def shutdown(self) -> None:
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()

    async def _weather_loop(self) -> None:
        while True:
            try:
                await weather.poll(self._settings, self._db)
            except Exception as exc:  # noqa: BLE001 - the loop must keep running
                logger.warning("weather poll failed: %s", _sanitize(exc))
            await asyncio.sleep(self._settings.weather_interval_minutes * 60)

    async def _checkin_loop(self) -> None:
        while True:
            try:
                await checkins.tick(self._settings, self._db)
            except Exception as exc:  # noqa: BLE001 - the loop must keep running
                logger.warning("check-in tick failed: %s", _sanitize(exc))
            await asyncio.sleep(CHECKIN_TICK_SECONDS)

    async def _forecast_loop(self) -> None:
        """Run the forecast job once per day at ``forecast_hour`` local time."""
        while True:
            delay = seconds_until_forecast(
                datetime.now(self._settings.timezone), self._settings.forecast_hour
            )
            await asyncio.sleep(delay)
            try:
                summary = await run_forecast_job(self._settings, self._db)
                logger.info("forecast job done: %s", summary)
            except Exception as exc:  # noqa: BLE001 - the loop must keep running
                logger.warning("forecast job failed: %s", _sanitize(exc))


def seconds_until_forecast(now_local: datetime, hour: int) -> float:
    """Seconds from ``now_local`` until the next occurrence of ``hour`` local."""
    target = now_local.replace(hour=hour, minute=0, second=0, microsecond=0)
    if target <= now_local:
        target += timedelta(days=1)
    return (target - now_local).total_seconds()
