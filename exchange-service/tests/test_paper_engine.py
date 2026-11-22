from decimal import Decimal

import pytest

from paper_engine import IdempotencyConflict, PaperMatchingEngine
from paper_ledger import PaperLedger


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

    bid = place(engine, "buy", "1.7", "101", "bid-batch-0001")
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


def test_quantities_below_market_step_are_rejected(engine):
    with pytest.raises(ValueError, match="multiple of"):
        place(engine, "buy", "0.00000000000000000001", "50000", "tiny-quantity-0001")


def test_order_trade_integrity_audit_passes_for_partial_fill_and_cancel(engine):
    ask = place(engine, "sell", "1", "100", "audit-ask-0001")
    bid = place(engine, "buy", "0.4", "105", "audit-bid-0001")
    engine.cancel_order(ask["id"])

    result = engine.audit_order_integrity()
    assert result["status"] == "ok"
    assert all(result["checks"].values())
    assert result["counts"]["orders"] == 2
    assert result["counts"]["trades"] == 1
    assert result["counts"]["orders_with_fills"] == 2


def test_order_trade_integrity_audit_detects_fill_quantity_tampering(engine):
    import sqlite3

    ask = place(engine, "sell", "1", "100", "tamper-ask-0001")
    place(engine, "buy", "0.4", "105", "tamper-bid-0001")
    with sqlite3.connect(engine.db_path) as db:
        db.execute("UPDATE paper_trades SET quantity='0.3'")

    result = engine.audit_order_integrity()
    assert result["status"] == "attention_required"
    assert result["checks"]["order_fill_quantities_reconcile"] is False
    assert result["counts"]["issues"]["fill_quantity_mismatches"] == 2
    assert ask["id"] in result["issues"]["fill_quantity_mismatches"]


def test_order_trade_integrity_audit_detects_orphan_trade(engine):
    import sqlite3

    with sqlite3.connect(engine.db_path) as db:
        db.execute(
            """INSERT INTO paper_trades
               (symbol,maker_order_id,taker_order_id,price,quantity,created_at)
               VALUES('BTC/USDT','missing-maker','missing-taker','100','1',1)"""
        )
    result = engine.audit_order_integrity()
    assert result["status"] == "attention_required"
    assert result["checks"]["trade_references_valid"] is False
    assert result["counts"]["issues"]["orphan_trades"] == 1


def test_order_funds_audit_matches_reserves_settlements_and_cancel_releases(engine):
    ledger = PaperLedger(engine.db_path)
    ledger.create_account("fund_seller")
    ledger.create_account("fund_buyer")
    ledger.create_account("fund_cancel")
    ledger.credit_paper_funds("fund_seller", "BTC", "2", "fund-seller-credit")
    ledger.credit_paper_funds("fund_buyer", "USDT", "500", "fund-buyer-credit")
    ledger.credit_paper_funds("fund_cancel", "USDT", "250", "fund-cancel-credit")

    ask = engine.place_limit_order(
        "BTC/USDT", "sell", "1", "100", "funds-sell-order-001", "fund_seller"
    )
    bid = engine.place_limit_order(
        "BTC/USDT", "buy", "0.5", "110", "funds-buy-order-001", "fund_buyer"
    )
    resting = engine.place_limit_order(
        "BTC/USDT", "buy", "1", "90", "funds-cancel-order-1", "fund_cancel"
    )
    engine.cancel_order(resting["id"])

    result = engine.audit_order_funds_integrity()
    assert result["status"] == "ok", result["issues"]
    assert all(result["checks"].values()), result["issues"]
    assert result["counts"]["orders_checked"] == 3
    assert result["counts"]["trades_checked"] == 1
    assert ask["status"] == "open"
    assert bid["status"] == "filled"


def test_order_funds_audit_detects_missing_ledger_reserve(engine):
    import sqlite3
    ledger = PaperLedger(engine.db_path)
    ledger.create_account("fund_missing")
    ledger.credit_paper_funds("fund_missing", "USDT", "200", "fund-missing-credit")
    order = engine.place_limit_order(
        "BTC/USDT", "buy", "1", "100", "fund-missing-order-001", "fund_missing"
    )
    with sqlite3.connect(engine.db_path) as db:
        db.execute(
            "DELETE FROM ledger_transactions WHERE idempotency_key=?",
            (f"paper-order-reserve:{order['id']}",),
        )
    result = engine.audit_order_funds_integrity()
    assert result["status"] == "attention_required"
    assert result["checks"]["order_reserves_match_ledger"] is False
    assert order["id"] in result["issues"]["missing_or_mismatched_reserves"]


def test_order_funds_audit_matches_when_resting_maker_is_the_buyer(engine):
    ledger = PaperLedger(engine.db_path)
    ledger.create_account("maker_buyer")
    ledger.create_account("taker_seller")
    ledger.credit_paper_funds("maker_buyer", "USDT", "500", "maker-buyer-credit")
    ledger.credit_paper_funds("taker_seller", "BTC", "2", "taker-seller-credit")

    bid = engine.place_limit_order(
        "BTC/USDT", "buy", "0.5", "110", "funds-maker-bid-0001", "maker_buyer"
    )
    ask = engine.place_limit_order(
        "BTC/USDT", "sell", "0.5", "100", "funds-taker-ask-001", "taker_seller"
    )

    result = engine.audit_order_funds_integrity()
    assert result["status"] == "ok", result["issues"]
    assert all(result["checks"].values()), result["issues"]
    assert result["counts"]["orders_checked"] == 2
    assert result["counts"]["trades_checked"] == 1
    assert bid["status"] == "open"
    assert ask["status"] == "filled"
