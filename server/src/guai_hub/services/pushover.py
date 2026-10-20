"""Pushover notifications (optional, privacy-safe).

Notifications are deliberately generic: they never contain the question text, answer
type, or options — they only tell the user that a check-in is waiting, and the answer
happens in the Pebble app. See spec/data-and-api.md.
"""

from __future__ import annotations

import logging

import httpx

from guai_hub.config import Settings

logger = logging.getLogger(__name__)

PUSHOVER_URL = "https://api.pushover.net/1/messages.json"
TITLE = "GUAI"
GENERIC_MESSAGE = "A check-in is waiting in the GUAI app on your watch."


def enabled(settings: Settings) -> bool:
    return bool(settings.pushover_token and settings.pushover_user)


async def send(settings: Settings, message: str = GENERIC_MESSAGE) -> None:
    """Send a Pushover message. Raises on failure; caller decides how to handle it."""
    if not enabled(settings):
        return
    client = httpx.AsyncClient(timeout=10)
    try:
        resp = await client.post(
            PUSHOVER_URL,
            data={
                "token": settings.pushover_token,
                "user": settings.pushover_user,
                "title": TITLE,
                "message": message,
            },
        )
        resp.raise_for_status()
    finally:
        await client.aclose()
