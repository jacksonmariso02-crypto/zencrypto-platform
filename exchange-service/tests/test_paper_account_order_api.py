from fastapi.testclient import TestClient

import main
from paper_engine import PaperMatchingEngine
from paper_ledger import PaperLedger


def test_account_paper_order_api_reserves_settles_and_cancels(monkeypatch, tmp_path):
    path = str(tmp_path / "api-account-paper.sqlite3")
    monkeypatch.setattr(main, "ADMIN_TOKEN", "paper-test-token")
    monkeypatch.setattr(main, "PAPER_ENGINE", PaperMatchingEngine(path))
    monkeypatch.setattr(main, "PAPER_LEDGER", PaperLedger(path))
    client = TestClient(main.app)
    headers = {"Authorization": "Bearer paper-test-token"}

    for account_id in ("api_buyer_01", "api_seller_01"):
        response = client.post(
            "/api/v1/paper/accounts",
            headers=headers,
            json={"account_id": account_id},
        )
        assert response.status_code == 200

    credit_buyer = client.post(
        "/api/v1/paper/accounts/api_buyer_01/credit",
        headers=headers,
        json={"asset": "USDT", "amount": "500", "idempotency_key": "api-buyer-credit-01"},
    )
    credit_seller = client.post(
        "/api/v1/paper/accounts/api_seller_01/credit",
        headers=headers,
        json={"asset": "BTC", "amount": "2", "idempotency_key": "api-seller-credit-01"},
    )
    assert credit_buyer.status_code == 200
    assert credit_seller.status_code == 200

    ask = client.post(
        "/api/v1/paper/accounts/api_seller_01/orders",
        headers=headers,
        json={
            "symbol": "BTC-USDT", "side": "sell", "quantity": "2",
            "price": "100", "client_order_id": "api-account-ask-01",
        },
    )
    assert ask.status_code == 200
    assert ask.json()["status"] == "open"

    bid = client.post(
        "/api/v1/paper/accounts/api_buyer_01/orders",
        headers=headers,
        json={
            "symbol": "BTC/USDT", "side": "buy", "quantity": "1",
            "price": "110", "client_order_id": "api-account-bid-01",
        },
    )
    assert bid.status_code == 200
    assert bid.json()["status"] == "filled"

    buyer_balances = client.get(
        "/api/v1/paper/accounts/api_buyer_01/balances", headers=headers
    )
    assert buyer_balances.json() == [
        {"asset": "BTC", "available": "1", "locked": "0"},
        {"asset": "USDT", "available": "400", "locked": "0"},
    ]
    seller_balances = client.get(
        "/api/v1/paper/accounts/api_seller_01/balances", headers=headers
    )
    assert seller_balances.json() == [
        {"asset": "BTC", "available": "0", "locked": "1"},
        {"asset": "USDT", "available": "100", "locked": "0"},
    ]

    account_orders = client.get(
        "/api/v1/paper/accounts/api_seller_01/orders", headers=headers
    )
    assert account_orders.status_code == 200
    assert len(account_orders.json()) == 1

    cancel = client.delete(
        f"/api/v1/paper/accounts/api_seller_01/orders/{ask.json()['id']}",
        headers=headers,
    )
    assert cancel.status_code == 200
    final_balances = client.get(
        "/api/v1/paper/accounts/api_seller_01/balances", headers=headers
    )
    assert final_balances.json() == [
        {"asset": "BTC", "available": "1", "locked": "0"},
        {"asset": "USDT", "available": "100", "locked": "0"},
    ]


def test_account_order_cannot_be_cancelled_from_another_account(monkeypatch, tmp_path):
    path = str(tmp_path / "api-account-isolation.sqlite3")
    monkeypatch.setattr(main, "ADMIN_TOKEN", "paper-test-token")
    monkeypatch.setattr(main, "PAPER_ENGINE", PaperMatchingEngine(path))
    monkeypatch.setattr(main, "PAPER_LEDGER", PaperLedger(path))
    client = TestClient(main.app)
    headers = {"Authorization": "Bearer paper-test-token"}

    for account_id in ("owner_001", "other_001"):
        client.post("/api/v1/paper/accounts", headers=headers, json={"account_id": account_id})
    client.post(
        "/api/v1/paper/accounts/owner_001/credit",
        headers=headers,
        json={"asset": "USDT", "amount": "100", "idempotency_key": "owner-credit-01"},
    )
    order = client.post(
        "/api/v1/paper/accounts/owner_001/orders",
        headers=headers,
        json={
            "symbol": "ETH/USDT", "side": "buy", "quantity": "1",
            "price": "50", "client_order_id": "owner-order-0001",
        },
    )
    denied = client.delete(
        f"/api/v1/paper/accounts/other_001/orders/{order.json()['id']}",
        headers=headers,
    )
    assert denied.status_code == 404
