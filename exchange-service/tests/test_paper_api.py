import pytest
from fastapi.testclient import TestClient

import main
from paper_engine import PaperMatchingEngine


@pytest.fixture
def api(monkeypatch, tmp_path):
    monkeypatch.setattr(main, "ADMIN_TOKEN", "paper-test-token")
    monkeypatch.setattr(
        main,
        "PAPER_ENGINE",
        PaperMatchingEngine(str(tmp_path / "api-paper.sqlite3")),
    )
    return TestClient(main.app)


def auth():
    return {"Authorization": "Bearer paper-test-token"}


def test_paper_api_requires_admin_token():
    old_token = main.ADMIN_TOKEN
    try:
        main.ADMIN_TOKEN = ""
        response = TestClient(main.app).get("/api/v1/paper/orders")
        assert response.status_code == 503
    finally:
        main.ADMIN_TOKEN = old_token


def test_paper_api_places_crossing_orders_and_exposes_trades(api):
    ask = api.post(
        "/api/v1/paper/orders",
        headers=auth(),
        json={
            "symbol": "BTC-USDT",
            "side": "sell",
            "quantity": "0.5",
            "price": "100",
            "client_order_id": "api-ask-order-01",
        },
    )
    assert ask.status_code == 200, ask.text
    bid = api.post(
        "/api/v1/paper/orders",
        headers=auth(),
        json={
            "symbol": "BTC/USDT",
            "side": "buy",
            "quantity": "0.2",
            "price": "105",
            "client_order_id": "api-bid-order-01",
        },
    )
    assert bid.status_code == 200, bid.text
    assert bid.json()["status"] == "filled"

    orders = api.get("/api/v1/paper/orders?symbol=BTC-USDT", headers=auth())
    assert orders.status_code == 200
    assert len(orders.json()) == 2
    book = api.get("/api/v1/paper/orderbook/BTC-USDT", headers=auth())
    assert book.status_code == 200
    assert book.json()["asks"] == [{"price": "100", "quantity": "0.3"}]
    trades = api.get("/api/v1/paper/trades?symbol=BTC-USDT", headers=auth())
    assert trades.status_code == 200
    assert len(trades.json()) == 1
    assert trades.json()[0]["price"] == "100"


def test_paper_api_idempotency_conflict_and_cancel(api):
    payload = {
        "symbol": "ETH/USDT",
        "side": "buy",
        "quantity": "1",
        "price": "2500",
        "client_order_id": "api-idempotent-01",
    }
    first = api.post("/api/v1/paper/orders", headers=auth(), json=payload)
    replay = api.post("/api/v1/paper/orders", headers=auth(), json=payload)
    assert first.status_code == 200
    assert replay.json()["id"] == first.json()["id"]

    changed = {**payload, "quantity": "2"}
    conflict = api.post("/api/v1/paper/orders", headers=auth(), json=changed)
    assert conflict.status_code == 409

    cancelled = api.delete(
        f"/api/v1/paper/orders/{first.json()['id']}", headers=auth()
    )
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "cancelled"


def test_paper_api_rejects_bad_limits_and_invalid_order(api):
    bad_limit = api.get("/api/v1/paper/orderbook/BTC-USDT?limit=999", headers=auth())
    assert bad_limit.status_code == 400
    bad_order = api.post(
        "/api/v1/paper/orders",
        headers=auth(),
        json={
            "symbol": "BTC-USDT",
            "side": "buy",
            "quantity": "NaN",
            "price": "100",
            "client_order_id": "api-invalid-order-01",
        },
    )
    assert bad_order.status_code == 422
