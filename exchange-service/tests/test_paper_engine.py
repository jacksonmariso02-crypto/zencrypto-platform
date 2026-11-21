from decimal import Decimal

import pytest

from paper_engine import IdempotencyConflict, PaperMatchingEngine


@pytest.fixture
def engine(tmp_path):
    return PaperMatchingEngine(str(tmp_path / "paper.sqlite3"))


def place(engine, side, qty, price, client_id):
    return engine.place_limit_order("BTC/USDT", side, qty, price, client_id)


def test_crossing_orders_match_at_resting_maker_price(engine):
    ask = place(engine, "sell", "1.0", "100.00", "ask-order-0001")
    bid = place(engine, "buy", "0.4", "105.00", "bid-order-0001")

    assert bid["status"] == "filled"
    assert bid["remaining"] == "0.0"
    updated_ask = engine.get_order(ask["id"])
    assert updated_ask["status"] == "partially_filled"
    assert Decimal(updated_ask["remaining"]) == Decimal("0.6")

    trades = engine.list_trades("BTC/USDT")
    assert len(trades) == 1
    assert trades[0]["price"] == "100.00"
    assert trades[0]["quantity"] == "0.4"


def test_price_priority_then_time_priority(engine):
    first = place(engine, "sell", "0.5", "99", "ask-first-0001")
    second = place(engine, "sell", "0.5", "99", "ask-second-0002")
    third = place(engine, "sell", "0.5", "100", "ask-third-0003")

    bid = place(engine, "buy", "1.2", "101", "bid-batch-0001")
    assert bid["status"] == "partially_filled"
    assert Decimal(bid["remaining"]) == Decimal("0.2")

    trades = engine.list_trades("BTC/USDT")
    assert [t["maker_order_id"] for t in reversed(trades)] == [
        first["id"], second["id"], third["id"]
    ]
    assert [t["price"] for t in reversed(trades)] == ["99", "99", "100"]


def test_non_crossing_orders_remain_on_book(engine):
    ask = place(engine, "sell", "1", "110", "ask-resting-0001")
    bid = place(engine, "buy", "1", "100", "bid-resting-0001")
    book = engine.orderbook("BTC/USDT")

    assert ask["status"] == "open"
    assert bid["status"] == "open"
    assert book["bids"] == [{"price": "100", "quantity": "1"}]
    assert book["asks"] == [{"price": "110", "quantity": "1"}]
    assert engine.list_trades("BTC/USDT") == []


def test_cancel_releases_remaining_quantity_from_book(engine):
    ask = place(engine, "sell", "1", "100", "ask-cancel-0001")
    cancelled = engine.cancel_order(ask["id"])
    assert cancelled["status"] == "cancelled"
    assert engine.orderbook("BTC/USDT")["asks"] == []
    with pytest.raises(ValueError):
        engine.cancel_order(ask["id"])


def test_client_order_id_is_idempotent(engine):
    first = place(engine, "buy", "0.25", "90", "retry-safe-0001")
    replay = place(engine, "buy", "0.25", "90", "retry-safe-0001")
    assert replay["id"] == first["id"]
    assert len(engine.list_orders()) == 1

    with pytest.raises(IdempotencyConflict):
        place(engine, "buy", "0.5", "90", "retry-safe-0001")


def test_invalid_decimals_and_symbols_are_rejected(engine):
    with pytest.raises(ValueError):
        place(engine, "buy", "NaN", "100", "invalid-qty-0001")
    with pytest.raises(ValueError):
        place(engine, "buy", "1", "Infinity", "invalid-price-0001")
    with pytest.raises(ValueError):
        engine.place_limit_order("BTC USDT", "buy", "1", "100", "invalid-symbol-0001")


def test_order_state_survives_engine_restart(tmp_path):
    path = str(tmp_path / "durable.sqlite3")
    first_engine = PaperMatchingEngine(path)
    placed = first_engine.place_limit_order(
        "ETH/USDT", "sell", "2", "3000", "restart-test-0001"
    )

    second_engine = PaperMatchingEngine(path)
    restored = second_engine.get_order(placed["id"])
    assert restored["status"] == "open"
    assert restored["remaining"] == "2"
    assert second_engine.orderbook("ETH/USDT")["asks"] == [
        {"price": "3000", "quantity": "2"}
    ]


def test_tiny_decimal_quantities_remain_visible_in_orderbook(engine):
    order = place(engine, "buy", "0.00000000000000000001", "0.0001", "tiny-quantity-0001")
    assert order["status"] == "open"
    assert engine.orderbook("BTC/USDT")["bids"] == [
        {"price": "0.0001", "quantity": "1E-20"}
    ]
