import os
import json
import sqlite3
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
                "base": "BTC",
                "quote": "USDT",
                "active": True,
                "precision": {"amount": 4, "price": 2},
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

    def fetch_ticker(self, symbol):
        return {"ask": 50000.0, "bid": 49999.0, "last": 50000.0}

    def fetch_balance(self):
        return {"free": {"USDT": 1000, "BTC": 0.5}}

    def create_order(self, symbol, order_type, side, amount, price, params=None):
        order = {"id": "fake-order-123", "symbol": symbol, "type": order_type, "side": side,
                 "amount": amount, "price": price, "status": "open", "filled": 0,
                 "remaining": amount, "cost": 0, "params": params or {}}
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


def test_public_market_info_returns_precision_and_limits(monkeypatch):
    fake = FakeOrderClient()
    monkeypatch.setattr(main, "exchange_client", lambda private=False: fake)
    response = client.get("/api/v1/markets/BTC-USDT/info")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["symbol"] == "BTC/USDT"
    assert body["base"] == "BTC"
    assert body["quote"] == "USDT"
    assert body["spot"] is True
    assert body["precision"] == {"amount": 4, "price": 2}
    assert body["limits"]["amount"]["min"] == 0.0001
    assert body["limits"]["cost"]["min"] == 10


def test_public_market_info_rejects_unknown_market(monkeypatch):
    fake = FakeOrderClient()
    monkeypatch.setattr(main, "exchange_client", lambda private=False: fake)
    response = client.get("/api/v1/markets/ETH-USDT/info")
    assert response.status_code == 404



def test_market_order_rejects_estimated_notional_below_exchange_minimum(monkeypatch):
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
            json={"symbol": "BTC/USDT", "side": "buy", "type": "market", "amount": "0.0001"},
        )
        assert response.status_code == 422
        assert "notional" in response.json()["detail"].lower()
        assert fake.created == []
    finally:
        main.ADMIN_TOKEN = old_token
        main.SANDBOX = old_sandbox
        main.LIVE_TRADING_ENABLED = old_live


def test_market_order_uses_ask_for_buy_notional_estimate(monkeypatch):
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
            json={"symbol": "BTC/USDT", "side": "buy", "type": "market", "amount": "0.0012"},
        )
        assert response.status_code == 200, response.text
        assert len(fake.created) == 1
        assert fake.created[0]["type"] == "market"
        assert fake.created[0]["price"] is None
    finally:
        main.ADMIN_TOKEN = old_token
        main.SANDBOX = old_sandbox
        main.LIVE_TRADING_ENABLED = old_live


def test_live_order_configuration_is_never_allowed(monkeypatch):
    old_sandbox = main.SANDBOX
    old_live = main.LIVE_TRADING_ENABLED
    old_token = main.ADMIN_TOKEN
    try:
        main.ADMIN_TOKEN = "test-token"
        main.SANDBOX = True
        main.LIVE_TRADING_ENABLED = True
        monkeypatch.setattr(
            main, "exchange_client",
            lambda private=False: (_ for _ in ()).throw(AssertionError("live route must not create an exchange client")),
        )
        response = client.post(
            "/api/v1/orders",
            headers={"Authorization": "Bearer test-token"},
            json={"symbol": "BTC/USDT", "side": "buy", "type": "market", "amount": "0.001"},
        )
        assert response.status_code == 403
        assert "not supported" in response.json()["detail"].lower()
    finally:
        main.ADMIN_TOKEN = old_token
        main.SANDBOX = old_sandbox
        main.LIVE_TRADING_ENABLED = old_live


def test_market_status_never_advertises_live_customer_order_routing():
    response = client.get("/api/v1/market-data/status")
    assert response.status_code == 200
    body = response.json()
    assert body["live_order_placement_enabled"] is False
    assert body["customer_order_routing_enabled"] is False


def _override_customer_user(user_id="customer-101"):
    main.app.dependency_overrides[main.require_platform_user] = lambda: {
        "id": user_id, "email": user_id + "@example.test", "role": "user"
    }


def _vault_test_key():
    import base64
    return base64.urlsafe_b64encode(b"z" * 32).decode("ascii")


def test_customer_binance_credentials_are_encrypted_and_user_scoped(monkeypatch):
    old_key = main.EXCHANGE_CREDENTIAL_ENCRYPTION_KEY
    try:
        main.EXCHANGE_CREDENTIAL_ENCRYPTION_KEY = _vault_test_key()
        _override_customer_user("customer-101")
        monkeypatch.setattr(
            main, "_verify_binance_customer_credentials",
            lambda api_key, api_secret: (
                {"read": True, "spot_trading": True, "withdrawals": False,
                 "margin_endpoints_used": False, "futures_endpoints_used": False},
                {"total": {"USDT": 50}},
            ),
        )
        response = client.post(
            "/api/v1/user/exchange-connections/binance",
            json={"api_key": "A" * 32, "api_secret": "S" * 40},
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["connected"] is True
        assert body["masked_api_key"] == "••••AAAA"
        assert body["live_order_routing_enabled"] is False
        assert "api_secret" not in body

        status = client.get("/api/v1/user/exchange-connections")
        assert status.status_code == 200
        assert status.json()["connections"][0]["connected"] is True

        with __import__("sqlite3").connect(main.PAPER_ENGINE_DB_PATH) as db:
            row = db.execute(
                "SELECT encrypted_credentials FROM customer_exchange_connections WHERE user_id=?",
                ("customer-101",),
            ).fetchone()
        assert row is not None
        assert b"S" * 20 not in bytes(row[0])

        _override_customer_user("customer-202")
        other_user = client.get("/api/v1/user/exchange-connections")
        assert other_user.status_code == 200
        assert other_user.json()["connections"][0]["connected"] is False

        _override_customer_user("customer-101")
        removed = client.delete("/api/v1/user/exchange-connections/binance")
        assert removed.status_code == 200
        assert removed.json()["disconnected"] is True
    finally:
        main.EXCHANGE_CREDENTIAL_ENCRYPTION_KEY = old_key
        main.app.dependency_overrides.pop(main.require_platform_user, None)


def test_customer_exchange_connection_requires_platform_session():
    response = client.get("/api/v1/user/exchange-connections")
    assert response.status_code == 401


def test_customer_exchange_connection_fails_closed_without_vault_key(monkeypatch):
    old_key = main.EXCHANGE_CREDENTIAL_ENCRYPTION_KEY
    try:
        main.EXCHANGE_CREDENTIAL_ENCRYPTION_KEY = ""
        _override_customer_user("customer-no-vault")
        monkeypatch.setattr(
            main, "_verify_binance_customer_credentials",
            lambda api_key, api_secret: (
                {"read": True, "spot_trading": True, "withdrawals": False}, {}
            ),
        )
        response = client.post(
            "/api/v1/user/exchange-connections/binance",
            json={"api_key": "A" * 32, "api_secret": "S" * 40},
        )
        assert response.status_code == 503
        assert "vault" in response.json()["detail"].lower()
    finally:
        main.EXCHANGE_CREDENTIAL_ENCRYPTION_KEY = old_key
        main.app.dependency_overrides.pop(main.require_platform_user, None)


def test_binance_credential_verification_rejects_withdrawal_permission(monkeypatch):
    class FakeCredentialClient:
        def privateGetAccountApiRestrictions(self):
            return {
                "enableReading": True,
                "enableSpotAndMarginTrading": True,
                "enableWithdrawals": True,
            }
        def fetch_balance(self):
            return {"total": {"USDT": 100}}
        def close(self):
            pass

    monkeypatch.setattr(main.ccxt, "binance", lambda config: FakeCredentialClient())
    try:
        main._verify_binance_customer_credentials("A" * 32, "S" * 40)
    except Exception as exc:
        assert getattr(exc, "status_code", None) == 422
        assert "withdrawal" in str(getattr(exc, "detail", "")).lower()
    else:
        raise AssertionError("API key with withdrawal permission was accepted")


def test_binance_credential_verification_requires_spot_trade_permission(monkeypatch):
    class FakeCredentialClient:
        def privateGetAccountApiRestrictions(self):
            return {
                "enableReading": True,
                "enableSpotAndMarginTrading": False,
                "enableWithdrawals": False,
            }
        def fetch_balance(self):
            return {"total": {}}
        def close(self):
            pass

    monkeypatch.setattr(main.ccxt, "binance", lambda config: FakeCredentialClient())
    try:
        main._verify_binance_customer_credentials("A" * 32, "S" * 40)
    except Exception as exc:
        assert getattr(exc, "status_code", None) == 422
        assert "spot" in str(getattr(exc, "detail", "")).lower()
    else:
        raise AssertionError("Read-only key was incorrectly accepted for trading")


def test_customer_order_preview_is_read_only_and_checks_balance(monkeypatch):
    fake = FakeOrderClient()
    old_limit = main.CUSTOMER_PREVIEW_MAX_NOTIONAL_USDT
    try:
        main.CUSTOMER_PREVIEW_MAX_NOTIONAL_USDT = main.Decimal("250")
        body = main.CustomerOrderPreviewRequest(
            symbol="BTC/USDT", side="buy", order_type="market", amount="0.001"
        )
        result = main._calculate_customer_order_preview(fake, body)
        assert main.Decimal(result["estimated_notional_usdt"]) == main.Decimal("50")
        assert main.Decimal(result["estimated_fee_buffer_usdt"]) == main.Decimal("0.05")
        assert result["required_asset"] == "USDT"
        assert result["order_submitted"] is False
        assert result["can_submit"] is False
        assert result["execution_status"] == "PREVIEW_ONLY"
        assert fake.created == []
    finally:
        main.CUSTOMER_PREVIEW_MAX_NOTIONAL_USDT = old_limit


def test_customer_order_preview_rejects_excessive_notional_without_submission():
    fake = FakeOrderClient()
    old_limit = main.CUSTOMER_PREVIEW_MAX_NOTIONAL_USDT
    try:
        main.CUSTOMER_PREVIEW_MAX_NOTIONAL_USDT = main.Decimal("25")
        body = main.CustomerOrderPreviewRequest(
            symbol="BTC/USDT", side="buy", order_type="market", amount="0.001"
        )
        try:
            main._calculate_customer_order_preview(fake, body)
        except Exception as exc:
            assert getattr(exc, "status_code", None) == 422
            assert "risk limit" in str(getattr(exc, "detail", "")).lower()
        else:
            raise AssertionError("Preview above per-order risk limit was accepted")
        assert fake.created == []
    finally:
        main.CUSTOMER_PREVIEW_MAX_NOTIONAL_USDT = old_limit


def test_customer_order_preview_rejects_insufficient_quote_balance():
    fake = FakeOrderClient()
    fake.fetch_balance = lambda: {"free": {"USDT": 10, "BTC": 0.5}}
    body = main.CustomerOrderPreviewRequest(
        symbol="BTC/USDT", side="buy", order_type="market", amount="0.001"
    )
    try:
        main._calculate_customer_order_preview(fake, body)
    except Exception as exc:
        assert getattr(exc, "status_code", None) == 409
        assert "insufficient" in str(getattr(exc, "detail", "")).lower()
    else:
        raise AssertionError("Insufficient quote balance was accepted")
    assert fake.created == []


def test_customer_order_preview_rejects_non_usdt_quote_market():
    fake = FakeOrderClient()
    markets = fake.load_markets()
    markets["BTC/USDC"] = {**markets["BTC/USDT"], "quote": "USDC"}
    fake.load_markets = lambda: markets
    body = main.CustomerOrderPreviewRequest(
        symbol="BTC/USDC", side="buy", order_type="market", amount="0.001"
    )
    try:
        main._calculate_customer_order_preview(fake, body)
    except Exception as exc:
        assert getattr(exc, "status_code", None) == 422
        assert "usdt" in str(getattr(exc, "detail", "")).lower()
    else:
        raise AssertionError("Unsupported quote asset was accepted")
    assert fake.created == []


def test_customer_order_preview_requires_limit_price():
    fake = FakeOrderClient()
    body = main.CustomerOrderPreviewRequest(
        symbol="BTC/USDT", side="buy", order_type="limit", amount="0.001"
    )
    try:
        main._calculate_customer_order_preview(fake, body)
    except Exception as exc:
        assert getattr(exc, "status_code", None) == 422
        assert "limit price" in str(getattr(exc, "detail", "")).lower()
    else:
        raise AssertionError("Limit order without price was accepted")
    assert fake.created == []


def test_customer_order_preview_rejects_invalid_precision():
    fake = FakeOrderClient()
    body = main.CustomerOrderPreviewRequest(
        symbol="BTC/USDT", side="buy", order_type="market", amount="0.00012345"
    )
    try:
        main._calculate_customer_order_preview(fake, body)
    except Exception as exc:
        assert getattr(exc, "status_code", None) == 422
        assert "precision" in str(getattr(exc, "detail", "")).lower()
    else:
        raise AssertionError("Amount with unsupported precision was accepted")
    assert fake.created == []


def test_customer_order_preview_requires_authenticated_platform_session():
    response = client.post(
        "/api/v1/user/orders/preview",
        json={"symbol": "BTC/USDT", "side": "buy", "order_type": "market", "amount": "0.001"},
    )
    assert response.status_code == 401


def test_customer_order_preview_rejects_unsafe_fee_buffer_configuration():
    fake = FakeOrderClient()
    old_fee = main.CUSTOMER_PREVIEW_FEE_BUFFER_RATE
    try:
        main.CUSTOMER_PREVIEW_FEE_BUFFER_RATE = main.Decimal("-0.01")
        body = main.CustomerOrderPreviewRequest(
            symbol="BTC/USDT", side="buy", order_type="market", amount="0.001"
        )
        try:
            main._calculate_customer_order_preview(fake, body)
        except Exception as exc:
            assert getattr(exc, "status_code", None) == 503
            assert "fee buffer" in str(getattr(exc, "detail", "")).lower()
        else:
            raise AssertionError("Unsafe negative fee buffer was accepted")
        assert fake.created == []
    finally:
        main.CUSTOMER_PREVIEW_FEE_BUFFER_RATE = old_fee


def test_customer_sell_preview_checks_available_base_asset():
    fake = FakeOrderClient()
    fake.fetch_balance = lambda: {"free": {"USDT": 1000, "BTC": 0.0005}}
    body = main.CustomerOrderPreviewRequest(
        symbol="BTC/USDT", side="sell", order_type="market", amount="0.001"
    )
    try:
        main._calculate_customer_order_preview(fake, body)
    except Exception as exc:
        assert getattr(exc, "status_code", None) == 409
        assert "insufficient" in str(getattr(exc, "detail", "")).lower()
    else:
        raise AssertionError("Sell preview with insufficient base balance was accepted")
    assert fake.created == []


def test_customer_order_idempotency_claim_replays_success_and_rejects_payload_change(tmp_path, monkeypatch):
    db_path = str(tmp_path / "customer-orders.sqlite3")
    monkeypatch.setattr(main, "PAPER_ENGINE_DB_PATH", db_path)
    main._init_customer_credential_store()
    request_hash = "a" * 64
    client_order_id, replay = main._claim_customer_order_request("user-1", "request-key-000001", request_hash)
    assert client_order_id.startswith("zc")
    assert replay is None
    stored = {"exchange_order_id": "123", "execution_status": "SUBMITTED"}
    main._finish_customer_order_request("user-1", "request-key-000001", "SUBMITTED", stored)
    replay = main._lookup_customer_order_request("user-1", "request-key-000001", request_hash)
    assert replay["exchange_order_id"] == "123"
    assert replay["idempotent_replay"] is True
    try:
        main._lookup_customer_order_request("user-1", "request-key-000001", "b" * 64)
    except Exception as exc:
        assert getattr(exc, "status_code", None) == 409
    else:
        raise AssertionError("Changed payload was accepted for an existing idempotency key")


def test_customer_order_idempotency_blocks_unknown_exchange_outcome(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "PAPER_ENGINE_DB_PATH", str(tmp_path / "unknown.sqlite3"))
    main._init_customer_credential_store()
    request_hash = "c" * 64
    main._claim_customer_order_request("user-2", "request-key-000002", request_hash)
    main._finish_customer_order_request("user-2", "request-key-000002", "UNKNOWN", None)
    try:
        main._lookup_customer_order_request("user-2", "request-key-000002", request_hash)
    except Exception as exc:
        assert getattr(exc, "status_code", None) == 409
        assert "reconciliation" in str(getattr(exc, "detail", "")).lower()
    else:
        raise AssertionError("Unknown exchange outcome was allowed to retry")


def test_customer_order_submission_is_disabled_by_default_even_with_customer_gate_enabled(monkeypatch):
    old_customer_gate = main.LIVE_CUSTOMER_ORDER_ROUTING_ENABLED
    old_live = main.LIVE_TRADING_ENABLED
    old_sandbox = main.SANDBOX
    main.app.dependency_overrides[main.require_platform_user] = lambda: {"id": "test-user", "email": "test@example.com", "role": "user"}
    try:
        main.LIVE_CUSTOMER_ORDER_ROUTING_ENABLED = True
        main.LIVE_TRADING_ENABLED = False
        main.SANDBOX = False
        response = client.post(
            "/api/v1/user/orders",
            headers={"Idempotency-Key": "request-key-000003"},
            json={"symbol": "BTC/USDT", "side": "buy", "order_type": "market", "amount": "0.001"},
        )
        assert response.status_code == 403
    finally:
        main.LIVE_CUSTOMER_ORDER_ROUTING_ENABLED = old_customer_gate
        main.LIVE_TRADING_ENABLED = old_live
        main.SANDBOX = old_sandbox
        main.app.dependency_overrides.pop(main.require_platform_user, None)


def test_customer_order_submission_requires_idempotency_key_when_gates_are_open(monkeypatch):
    old_customer_gate = main.LIVE_CUSTOMER_ORDER_ROUTING_ENABLED
    old_live = main.LIVE_TRADING_ENABLED
    old_sandbox = main.SANDBOX
    main.app.dependency_overrides[main.require_platform_user] = lambda: {"id": "test-user", "email": "test@example.com", "role": "user"}
    try:
        main.LIVE_CUSTOMER_ORDER_ROUTING_ENABLED = True
        main.LIVE_TRADING_ENABLED = True
        main.SANDBOX = False
        response = client.post(
            "/api/v1/user/orders",
            json={"symbol": "BTC/USDT", "side": "buy", "order_type": "market", "amount": "0.001"},
        )
        assert response.status_code == 400
    finally:
        main.LIVE_CUSTOMER_ORDER_ROUTING_ENABLED = old_customer_gate
        main.LIVE_TRADING_ENABLED = old_live
        main.SANDBOX = old_sandbox
        main.app.dependency_overrides.pop(main.require_platform_user, None)


def test_customer_order_submission_uses_customer_credentials_and_idempotent_replay(tmp_path, monkeypatch):
    db_path = str(tmp_path / "submit.sqlite3")
    monkeypatch.setattr(main, "PAPER_ENGINE_DB_PATH", db_path)
    main._init_customer_credential_store()
    with sqlite3.connect(db_path) as db:
        db.execute(
            """INSERT INTO customer_exchange_connections
               (user_id, exchange_id, encrypted_credentials, masked_api_key,
                permissions_json, status, connected_at, last_verified_at)
               VALUES (?, 'binance', ?, ?, ?, 'connected', ?, ?)""",
            ("submit-user", b"test-envelope", "••••1234",
             json.dumps({"read": True, "spot_trading": True, "withdrawals": False}), 1, 1),
        )
    fake = FakeOrderClient()
    old_customer_gate = main.LIVE_CUSTOMER_ORDER_ROUTING_ENABLED
    old_live = main.LIVE_TRADING_ENABLED
    old_sandbox = main.SANDBOX
    old_limit = main.CUSTOMER_PREVIEW_MAX_NOTIONAL_USDT
    main.app.dependency_overrides[main.require_platform_user] = lambda: {"id": "submit-user", "email": "test@example.com", "role": "user"}
    monkeypatch.setattr(main, "_decrypt_customer_credentials", lambda user_id, envelope: {"api_key": "customer-key", "api_secret": "customer-secret"})
    monkeypatch.setattr(main.ccxt, "binance", lambda config: fake)
    try:
        main.LIVE_CUSTOMER_ORDER_ROUTING_ENABLED = True
        main.LIVE_TRADING_ENABLED = True
        main.SANDBOX = False
        main.CUSTOMER_PREVIEW_MAX_NOTIONAL_USDT = main.Decimal("250")
        headers = {"Idempotency-Key": "request-key-000004"}
        payload = {"symbol": "BTC/USDT", "side": "buy", "order_type": "market", "amount": "0.001"}
        first = client.post("/api/v1/user/orders", headers=headers, json=payload)
        assert first.status_code == 200, first.text
        assert first.json()["execution_status"] == "SUBMITTED"
        assert first.json()["idempotent_replay"] is False
        second = client.post("/api/v1/user/orders", headers=headers, json=payload)
        assert second.status_code == 200, second.text
        assert second.json()["idempotent_replay"] is True
        assert len(fake.created) == 1
        assert fake.created[0]["side"] == "buy"
    finally:
        main.LIVE_CUSTOMER_ORDER_ROUTING_ENABLED = old_customer_gate
        main.LIVE_TRADING_ENABLED = old_live
        main.SANDBOX = old_sandbox
        main.CUSTOMER_PREVIEW_MAX_NOTIONAL_USDT = old_limit
        main.app.dependency_overrides.pop(main.require_platform_user, None)
