"""Tiny time helpers: UTC ISO timestamps and interval-grid flooring."""

from __future__ import annotations

from datetime import UTC, datetime


def utc_now_iso() -> str:
    """Current UTC time as an ISO string with the ``Z`` suffix."""
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def floor_interval_utc(when: datetime, minutes: int) -> str:
    """Floor ``when`` (UTC) to a fixed interval grid and return as ISO-Z text."""
    epoch = int(when.timestamp())
    bucket = epoch - (epoch % (minutes * 60))
    return datetime.fromtimestamp(bucket, UTC).isoformat().replace("+00:00", "Z")
