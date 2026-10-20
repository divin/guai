"""Config/location/geo endpoints tests (Step 2 gate)."""

import httpx

from guai_hub.services import weather
from tests.fakes import FakeAsyncClient, FakeResponse


def test_config_defaults_no_location(client):
    body = client.get("/api/config").json()
    assert body["location"] is None
    assert "server_version" in body


def test_geo_search_requires_query(client):
    assert client.get("/api/geo/search").status_code == 422
    assert client.get("/api/geo/search", params={"q": ""}).status_code == 422


def test_geo_search_proxies_results(monkeypatch, client):
    results = {
        "results": [
            {
                "name": "Berlin",
                "latitude": 52.52,
                "longitude": 13.405,
                "country": "Germany",
                "admin1": "Berlin",
            }
        ]
    }
    fake = FakeAsyncClient([FakeResponse(results)])
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: fake)

    body = client.get("/api/geo/search", params={"q": "Berlin"}).json()
    assert body[0]["name"] == "Berlin"
    assert body[0]["latitude"] == 52.52


def test_put_location_stores_and_returns(monkeypatch, client):
    async def _noop_poll(*args, **kwargs):
        return None

    monkeypatch.setattr(weather, "poll", _noop_poll)

    body = client.put(
        "/api/config/location",
        json={"name": "Berlin", "latitude": 52.52, "longitude": 13.405},
    )
    assert body.status_code == 200
    assert body.json() == {"name": "Berlin", "latitude": 52.52, "longitude": 13.405}

    config = client.get("/api/config").json()
    assert config["location"] == {"name": "Berlin", "latitude": 52.52, "longitude": 13.405}


def test_put_location_validates_bounds(client):
    assert (
        client.put(
            "/api/config/location",
            json={"name": "X", "latitude": 95, "longitude": 0},
        ).status_code
        == 422
    )
