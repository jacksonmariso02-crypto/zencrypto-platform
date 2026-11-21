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



class FakeOrderClient:
    def __init__(self):
        self.created = []

    def set_sandbox_mode(self, enabled):
        pass

    def load_markets(self):
        return {
            "BTC/USDT": {
                "spot": True,
                "limits": {
                    "amount": {"min": 0.0001, "max": 10},
                    "price": {"min": 1, "max": 1000000},
                    "cost": {"min": 10, "max": 1000000},
                },
            }
        }

    def amount_to_precision(self, symbol, amount):
        return format(float(amount), ".4f")

    def price_to_precision(self, symbol, price):
        return format(float(price), ".2f")

    def create_order(self, symbol, order_type, side, amount, price):
        order = {"symbol": symbol, "type": order_type, "side": side, "amount": amount, "price": price}
        self.created.append(order)
        return order

    def close(self):
        pass


def test_order_rejects_non_finite_amount_before_exchange_call(monkeypatch):
    fake = FakeOrderClient()
    old_token = main.ADMIN_TOKEN
    old_sandbox = main.SANDBOX
    old_live = main.LIVE_TRADING_ENABLED
    try:
        main.ADMIN_TOKEN = "test-token"
        main.SANDBOX = True
        main.LIVE_TRADING_ENABLED = False
        monkeypatch.setattr(main, "exchange_client", lambda private=False: fake)
        response = client.post(
            "/api/v1/orders",
            headers={"Authorization": "Bearer test-token"},
            json={"symbol": "BTC/USDT", "side": "buy", "type": "market", "amount": "NaN"},
        )
        assert response.status_code == 422
        assert fake.created == []
    finally:
        main.ADMIN_TOKEN = old_token
        main.SANDBOX = old_sandbox
        main.LIVE_TRADING_ENABLED = old_live


def test_order_rejects_amount_that_exchange_would_round(monkeypatch):
    fake = FakeOrderClient()
    old_token = main.ADMIN_TOKEN
    old_sandbox = main.SANDBOX
    old_live = main.LIVE_TRADING_ENABLED
    try:
        main.ADMIN_TOKEN = "test-token"
        main.SANDBOX = True
        main.LIVE_TRADING_ENABLED = False
        monkeypatch.setattr(main, "exchange_client", lambda private=False: fake)
        response = client.post(
            "/api/v1/orders",
            headers={"Authorization": "Bearer test-token"},
            json={"symbol": "BTC/USDT", "side": "buy", "type": "limit", "amount": "0.00012345", "price": "50000.00"},
        )
        assert response.status_code == 422
        assert "precision" in response.json()["detail"].lower()
        assert fake.created == []
    finally:
        main.ADMIN_TOKEN = old_token
        main.SANDBOX = old_sandbox
        main.LIVE_TRADING_ENABLED = old_live


def test_order_accepts_exact_market_precision_and_uses_sandbox_client(monkeypatch):
    fake = FakeOrderClient()
    old_token = main.ADMIN_TOKEN
    old_sandbox = main.SANDBOX
    old_live = main.LIVE_TRADING_ENABLED
    try:
        main.ADMIN_TOKEN = "test-token"
        main.SANDBOX = True
        main.LIVE_TRADING_ENABLED = False
        monkeypatch.setattr(main, "exchange_client", lambda private=False: fake)
        response = client.post(
            "/api/v1/orders",
            headers={"Authorization": "Bearer test-token"},
            json={"symbol": "BTC/USDT", "side": "buy", "type": "limit", "amount": "0.0012", "price": "50000.00"},
        )
        assert response.status_code == 200, response.text
        assert len(fake.created) == 1
        assert fake.created[0]["amount"] == 0.0012
        assert fake.created[0]["price"] == 50000.0
    finally:
        main.ADMIN_TOKEN = old_token
        main.SANDBOX = old_sandbox
        main.LIVE_TRADING_ENABLED = old_live


def test_limit_order_rejects_notional_below_market_minimum(monkeypatch):
    fake = FakeOrderClient()
    old_token = main.ADMIN_TOKEN
    old_sandbox = main.SANDBOX
    old_live = main.LIVE_TRADING_ENABLED
    try:
        main.ADMIN_TOKEN = "test-token"
        main.SANDBOX = True
        main.LIVE_TRADING_ENABLED = False
        monkeypatch.setattr(main, "exchange_client", lambda private=False: fake)
        response = client.post(
            "/api/v1/orders",
            headers={"Authorization": "Bearer test-token"},
            json={"symbol": "BTC/USDT", "side": "buy", "type": "limit", "amount": "0.0001", "price": "50000.00"},
        )
        assert response.status_code == 422
        assert "notional" in response.json()["detail"].lower()
        assert fake.created == []
    finally:
        main.ADMIN_TOKEN = old_token
        main.SANDBOX = old_sandbox
        main.LIVE_TRADING_ENABLED = old_live
