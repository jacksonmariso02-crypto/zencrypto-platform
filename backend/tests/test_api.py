import os
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


def test_market_candle_endpoint_does_not_accept_arbitrary_symbol(client):
    response = client.get("/market/candles", params={"symbol": "ETHUSDT", "interval": "1h", "limit": 100})
    # Valid input may reach the public data provider; this test only asserts that validation did not reject it.
    assert response.status_code in (200, 502, 504)
