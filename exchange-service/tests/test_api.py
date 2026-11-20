import os
import sys
from pathlib import Path

os.environ.setdefault("EXCHANGE_SANDBOX", "true")
os.environ.setdefault("LIVE_TRADING_ENABLED", "false")

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient
import main

client = TestClient(main.app)


def test_health_reports_sandbox_defaults():
    response = client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["sandbox"] is True
    assert body["live_trading_enabled"] is False


def test_invalid_symbol_is_rejected_before_exchange_call():
    try:
        main.normalize_symbol("BTC USDT")
    except Exception as exc:
        assert getattr(exc, "status_code", None) == 400
    else:
        raise AssertionError("Invalid symbol was accepted")


def test_symbol_normalization_accepts_common_formats():
    assert main.normalize_symbol("BTC-USDT") == "BTC/USDT"
    assert main.normalize_symbol("eth/usdt") == "ETH/USDT"


def test_private_endpoint_requires_admin_configuration():
    old = main.ADMIN_TOKEN
    try:
        main.ADMIN_TOKEN = ""
        response = client.get("/api/v1/account/balance")
        assert response.status_code == 503
    finally:
        main.ADMIN_TOKEN = old


def test_live_order_is_disabled_by_default():
    old_sandbox = main.SANDBOX
    old_live = main.LIVE_TRADING_ENABLED
    old_token = main.ADMIN_TOKEN
    try:
        main.SANDBOX = False
        main.LIVE_TRADING_ENABLED = False
        main.ADMIN_TOKEN = "test-token"
        response = client.post(
            "/api/v1/orders",
            headers={"Authorization": "Bearer test-token"},
            json={"symbol": "BTC/USDT", "side": "buy", "type": "market", "amount": "0.001"},
        )
        assert response.status_code == 403
    finally:
        main.SANDBOX = old_sandbox
        main.LIVE_TRADING_ENABLED = old_live
        main.ADMIN_TOKEN = old_token
