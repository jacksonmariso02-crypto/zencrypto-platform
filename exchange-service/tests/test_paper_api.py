import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

import main
from paper_engine import PaperMatchingEngine
from market_stream import MarketStreamHub
from orderbook_depth import build_depth


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


def test_public_market_metadata_and_market_detail(api):
    markets = api.get("/api/v1/markets")
    assert markets.status_code == 200
    assert any(item["symbol"] == "BTC/USDT" for item in markets.json())

    btc = api.get("/api/v1/markets/BTC-USDT")
    assert btc.status_code == 200
    assert btc.json()["tick_size"] == "0.01"
    assert btc.json()["step_size"] == "0.000001"
    assert btc.json()["min_notional"] == "5"

    disabled = api.get("/api/v1/markets/UNKNOWN-USDT")
    assert disabled.status_code == 404


def test_paper_order_api_rejects_price_and_quantity_precision(api):
    bad_price = api.post(
        "/api/v1/paper/orders",
        headers=auth(),
        json={
            "symbol": "BTC/USDT", "side": "buy", "quantity": "0.001",
            "price": "50000.001", "client_order_id": "precision-price-01",
        },
    )
    assert bad_price.status_code == 422
    assert "Price" in bad_price.json()["detail"]

    bad_quantity = api.post(
        "/api/v1/paper/orders",
        headers=auth(),
        json={
            "symbol": "BTC/USDT", "side": "buy", "quantity": "0.0000001",
            "price": "50000", "client_order_id": "precision-qty-0001",
        },
    )
    assert bad_quantity.status_code == 422
    assert "Quantity" in bad_quantity.json()["detail"]


def test_paper_order_api_rejects_below_minimum_notional(api):
    response = api.post(
        "/api/v1/paper/orders",
        headers=auth(),
        json={
            "symbol": "BTC/USDT", "side": "buy", "quantity": "0.0001",
            "price": "1000", "client_order_id": "min-notional-0001",
        },
    )
    assert response.status_code == 422
    assert "notional" in response.json()["detail"].lower()


def test_paper_candle_api_uses_only_local_paper_fills(api):
    ask = api.post(
        "/api/v1/paper/orders",
        headers=auth(),
        json={
            "symbol": "BTC/USDT", "side": "sell", "quantity": "0.1",
            "price": "50000", "client_order_id": "paper-candle-ask-01",
        },
    )
    bid = api.post(
        "/api/v1/paper/orders",
        headers=auth(),
        json={
            "symbol": "BTC/USDT", "side": "buy", "quantity": "0.1",
            "price": "50001", "client_order_id": "paper-candle-bid-01",
        },
    )
    assert ask.status_code == 200
    assert bid.status_code == 200

    response = api.get("/api/v1/paper/markets/BTC-USDT/candles?timeframe=1m")
    assert response.status_code == 200
    candles = response.json()
    assert len(candles) == 1
    assert candles[0]["open"] == "50000"
    assert candles[0]["high"] == "50000"
    assert candles[0]["low"] == "50000"
    assert candles[0]["close"] == "50000"
    assert candles[0]["volume"] == "0.1"
    assert isinstance(candles[0]["timestamp"], int)


def test_paper_candle_api_rejects_invalid_query(api):
    bad_timeframe = api.get(
        "/api/v1/paper/markets/BTC-USDT/candles?timeframe=2m"
    )
    assert bad_timeframe.status_code == 400
    bad_limit = api.get(
        "/api/v1/paper/markets/BTC-USDT/candles?limit=1001"
    )
    assert bad_limit.status_code == 400


def test_paper_trade_websocket_streams_new_fills_and_resumes(api):
    with api.websocket_connect("/ws/v1/paper/trades?symbol=BTC-USDT") as websocket:
        ready = websocket.receive_json()
        assert ready["type"] == "ready"
        assert ready["channel"] == "paper.trades"
        assert ready["symbol"] == "BTC/USDT"

        ask = api.post(
            "/api/v1/paper/orders",
            headers=auth(),
            json={
                "symbol": "BTC/USDT", "side": "sell", "quantity": "0.1",
                "price": "50000", "client_order_id": "ws-paper-ask-01",
            },
        )
        bid = api.post(
            "/api/v1/paper/orders",
            headers=auth(),
            json={
                "symbol": "BTC/USDT", "side": "buy", "quantity": "0.1",
                "price": "50001", "client_order_id": "ws-paper-bid-01",
            },
        )
        assert ask.status_code == 200
        assert bid.status_code == 200

        event = websocket.receive_json()
        assert event["type"] == "trade"
        assert event["data"]["symbol"] == "BTC/USDT"
        assert event["data"]["price"] == "50000"
        assert event["data"]["quantity"] == "0.1"
        trade_id = event["data"]["id"]

    with api.websocket_connect(
        f"/ws/v1/paper/trades?symbol=BTC-USDT&since_id={trade_id}"
    ) as websocket:
        resumed = websocket.receive_json()
        assert resumed["type"] == "ready"
        assert resumed["cursor"] == trade_id


def test_paper_trade_websocket_rejects_invalid_query(api):
    with pytest.raises(WebSocketDisconnect) as disconnected:
        with api.websocket_connect("/ws/v1/paper/trades?symbol=NOT%20VALID"):
            pass
    assert disconnected.value.code == 1008


def test_external_market_websocket_streams_shared_public_snapshot(api, monkeypatch):
    async def fetch_snapshot(symbol):
        return {
            "source": "external_exchange",
            "exchange": "binance",
            "market_type": "spot",
            "symbol": symbol,
            "ticker": {"last": 65000},
            "orderbook": {"bids": [[64999, 1]], "asks": [[65001, 1]]},
            "depth": build_depth(
                symbol, [[64999, 1]], [[65001, 1]], source="external_exchange"
            ),
        }

    monkeypatch.setattr(
        main, "MARKET_STREAM_HUB",
        MarketStreamHub(fetch_snapshot, interval_seconds=2),
    )
    with api.websocket_connect("/ws/v1/markets/BTC-USDT") as websocket:
        ready = websocket.receive_json()
        assert ready["type"] == "ready"
        assert ready["source"] == "external_exchange"
        assert ready["symbol"] == "BTC/USDT"

        event = websocket.receive_json()
        assert event["type"] == "market_snapshot"
        assert event["data"]["source"] == "external_exchange"
        assert event["data"]["ticker"]["last"] == 65000
        assert event["data"]["orderbook"]["asks"][0][0] == 65001
        assert event["data"]["depth"]["source"] == "external_exchange"
        assert event["data"]["depth"]["metrics"]["best_ask"] == "65001"


def test_external_market_websocket_rejects_invalid_symbol(api):
    with pytest.raises(WebSocketDisconnect) as disconnected:
        with api.websocket_connect("/ws/v1/markets/BTC%20USDT"):
            pass
    assert disconnected.value.code == 1008


def test_market_data_status_identifies_external_spot_source(api):
    response = api.get("/api/v1/market-data/status")
    assert response.status_code == 200
    assert response.json()["source"] == "external_exchange"
    assert response.json()["market_type"] == "spot"
    assert response.json()["transport"] == "websocket"


def test_paper_depth_endpoint_requires_admin_and_returns_metrics(api):
    ask = api.post(
        "/api/v1/paper/orders",
        headers=auth(),
        json={
            "symbol": "BTC/USDT", "side": "sell", "quantity": "0.001",
            "price": "50010", "client_order_id": "depth-api-ask-01",
        },
    )
    bid = api.post(
        "/api/v1/paper/orders",
        headers=auth(),
        json={
            "symbol": "BTC/USDT", "side": "buy", "quantity": "0.001",
            "price": "50000", "client_order_id": "depth-api-bid-01",
        },
    )
    assert ask.status_code == 200
    assert bid.status_code == 200

    response = api.get(
        "/api/v1/paper/orderbook/BTC-USDT/depth?limit=10",
        headers=auth(),
    )
    assert response.status_code == 200
    assert response.json()["source"] == "zencrypto_paper"
    assert response.json()["metrics"]["best_bid"] == "50000"
    assert response.json()["metrics"]["best_ask"] == "50010"
    assert api.get("/api/v1/paper/orderbook/BTC-USDT/depth").status_code == 401


def test_external_depth_endpoint_normalizes_provider_book(api, monkeypatch):
    class FakeClient:
        def fetch_order_book(self, symbol, limit):
            assert symbol == "BTC/USDT"
            assert limit == 5
            return {
                "bids": [[50000, 1], [49990, 2]],
                "asks": [[50010, 3], [50020, 4]],
            }

        def close(self):
            return None

    monkeypatch.setattr(main, "exchange_client", lambda: FakeClient())
    response = api.get("/api/v1/markets/BTC-USDT/depth?limit=5")
    assert response.status_code == 200
    payload = response.json()
    assert payload["source"] == "external_exchange"
    assert payload["metrics"]["best_bid"] == "50000"
    assert payload["metrics"]["best_ask"] == "50010"
    assert payload["bids"][0]["cumulative_quantity"] == "1"
    assert payload["asks"][1]["cumulative_quantity"] == "7"


def test_paper_orderbook_websocket_sends_snapshot_then_price_level_delta(api, monkeypatch):
    async def fetch_snapshot(symbol):
        return await main.run_in_threadpool(main.PAPER_ENGINE.orderbook_depth, symbol, 100)

    monkeypatch.setattr(
        main, "PAPER_ORDERBOOK_HUB",
        main.PaperOrderBookHub(fetch_snapshot, interval_seconds=0.25),
    )
    with api.websocket_connect("/ws/v1/paper/orderbook/BTC-USDT") as websocket:
        ready = websocket.receive_json()
        assert ready["type"] == "ready"
        assert ready["source"] == "zencrypto_paper"
        assert ready["symbol"] == "BTC/USDT"

        initial = websocket.receive_json()
        assert initial["type"] == "depth_snapshot"
        assert initial["sequence"] == 1
        assert initial["data"]["bids"] == []

        response = api.post(
            "/api/v1/paper/orders",
            headers=auth(),
            json={
                "symbol": "BTC/USDT", "side": "buy", "quantity": "0.001",
                "price": "40000", "client_order_id": "ws-depth-bid-0001",
            },
        )
        assert response.status_code == 200

        delta = websocket.receive_json()
        assert delta["type"] == "depth_delta"
        assert delta["sequence"] == 2
        assert delta["previous_sequence"] == 1
        assert delta["bids"] == [["40000", "0.001"]]
        assert delta["asks"] == []


def test_paper_orderbook_websocket_rejects_invalid_symbol(api):
    with pytest.raises(WebSocketDisconnect) as disconnected:
        with api.websocket_connect("/ws/v1/paper/orderbook/NOT%20VALID"):
            pass
    assert disconnected.value.code == 1008
