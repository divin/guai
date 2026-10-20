"""Reusable fake ``httpx.AsyncClient`` for weather/config tests."""

from __future__ import annotations

import httpx


class FakeResponse:
    def __init__(self, payload, status: int = 200) -> None:
        self._payload = payload
        self.status_code = status

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("boom", request=None, response=self)

    def json(self) -> dict:
        return self._payload


class FakeAsyncClient:
    """Stand-in for ``httpx.AsyncClient``: plays responses in order and records calls."""

    def __init__(self, responses, timeout: float | None = None) -> None:
        self.responses = list(responses)
        self.calls: list[tuple[str, dict | None]] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def aclose(self) -> None:
        return None

    async def get(self, url: str, params: dict | None = None, **kwargs):
        self.calls.append((url, params))
        return self.responses.pop(0)
