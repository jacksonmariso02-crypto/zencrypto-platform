import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import main


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "DB", str(tmp_path / "zencrypto-test.db"))
    monkeypatch.setenv("APP_SECRET", "test-only-secret-that-is-long-enough-for-tests")
    monkeypatch.setenv("ENABLE_PAPER_TRADING", "false")
    with TestClient(main.app) as test_client:
        yield test_client


def test_health_is_liveness_only(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert "X-Content-Type-Options" in response.headers
    assert response.headers["X-Frame-Options"] == "DENY"
    assert "frame-ancestors 'none'" in response.headers["Content-Security-Policy"]


def test_readiness_fails_closed_without_required_secrets(client, monkeypatch):
    monkeypatch.delenv("ZEROX_API_KEY", raising=False)
    monkeypatch.delenv("APP_SECRET", raising=False)
    monkeypatch.delenv("CORS_ORIGINS", raising=False)
    response = client.get("/ready")
    assert response.status_code == 503
    checks = response.json()["checks"]
    assert checks["dex_provider_key_configured"] is False
    assert checks["explicit_cors_origins_configured"] is False
    assert checks["paper_trading_disabled"] is True
    assert checks["persistent_app_secret_configured"] is False


@pytest.mark.parametrize(
    "params",
    [
        {"symbol": "UNKNOWN", "interval": "1h", "limit": 100},
        {"symbol": "BTCUSDT", "interval": "2m", "limit": 100},
        {"symbol": "BTCUSDT", "interval": "1h", "limit": 10},
        {"symbol": "BTCUSDT", "interval": "1h", "limit": 501},
    ],
)
def test_candle_endpoint_rejects_unsupported_parameters(client, params):
    response = client.get("/market/candles", params=params)
    assert response.status_code == 422


def test_legacy_paper_routes_are_disabled_by_default(client):
    response = client.post("/auth/login", json={"email": "nobody@example.com", "password": "not-a-real-password"})
    assert response.status_code == 404
    assert "disabled" in response.json()["detail"].lower()

def test_candle_endpoint_normalizes_valid_binance_rows_without_network(client, monkeypatch):
    class FakeResponse:
        status_code = 200

        def json(self):
            return [
                [1700000000000, "100", "110", "95", "105", "12.5"],
                ["bad-timestamp", "100", "110", "95", "105", "12.5"],
                [1700003600000, "100", "90", "95", "105", "12.5"],
            ]

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def get(self, *args, **kwargs):
            return FakeResponse()

    monkeypatch.setattr(main.httpx, "AsyncClient", FakeAsyncClient)
    response = client.get("/market/candles", params={"symbol": "btcusdt", "interval": "1h", "limit": 100})
    assert response.status_code == 200
    payload = response.json()
    assert payload["symbol"] == "BTCUSDT"
    assert payload["interval"] == "1h"
    assert payload["source"] == "Binance public market data"
    assert payload["candles"] == [
        {"time": 1700000000, "open": 100.0, "high": 110.0, "low": 95.0, "close": 105.0, "volume": 12.5}
    ]


def test_chain_history_rejects_invalid_address_before_provider_call(client):
    response = client.get("/chain/history", params={"chainId": 1, "address": "not-an-address"})
    assert response.status_code == 422
    assert "Invalid wallet address" in response.json()["detail"]


def test_chain_history_rejects_unsupported_network(client):
    response = client.get(
        "/chain/history",
        params={"chainId": 11155111, "address": "0x" + "1" * 40},
    )
    assert response.status_code == 422
    assert "Ethereum and Base mainnet" in response.json()["detail"]
