import sys
from decimal import Decimal
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from cost_basis import calculate_fifo_cost_basis


def test_fifo_realized_pnl_includes_buy_and_sell_fees():
    result = calculate_fifo_cost_basis([
        {"id": "b1", "symbol": "ETH", "side": "buy", "quantity": "2", "price": "100", "fee": "2", "timestamp": 1},
        {"id": "s1", "symbol": "ETH", "side": "sell", "quantity": "1", "price": "130", "fee": "1", "timestamp": 2},
    ])
    eth = result["symbols"]["ETH"]
    assert Decimal(eth["realized_pnl"]) == Decimal("28")
    assert Decimal(eth["open_quantity"]) == Decimal("1")
    assert Decimal(eth["open_cost_basis"]) == Decimal("101")
    assert result["method"] == "FIFO"
    assert result["account_wide_pnl_available"] is False


def test_fifo_consumes_oldest_lot_first_across_multiple_buys():
    result = calculate_fifo_cost_basis([
        {"id": "b1", "symbol": "BTC", "side": "buy", "quantity": "1", "price": "100", "fee": "0", "timestamp": 1},
        {"id": "b2", "symbol": "BTC", "side": "buy", "quantity": "1", "price": "200", "fee": "0", "timestamp": 2},
        {"id": "s1", "symbol": "BTC", "side": "sell", "quantity": "1.5", "price": "250", "fee": "0", "timestamp": 3},
    ])
    btc = result["symbols"]["BTC"]
    assert Decimal(btc["realized_pnl"]) == Decimal("175")
    assert Decimal(btc["open_quantity"]) == Decimal("0.5")
    assert Decimal(btc["open_cost_basis"]) == Decimal("100")
    assert btc["open_lots"][0]["source_trade_id"] == "b2"


@pytest.mark.parametrize("trades", [
    [{"id": "x", "symbol": "ETH", "side": "sell", "quantity": "1", "price": "10", "fee": "0", "timestamp": 1}],
    [{"id": "x", "symbol": "ETH", "side": "buy", "quantity": "0", "price": "10", "fee": "0", "timestamp": 1}],
    [{"id": "x", "symbol": "ETH", "side": "buy", "quantity": "1", "price": "10", "fee": "-1", "timestamp": 1}],
    [
        {"id": "x", "symbol": "ETH", "side": "buy", "quantity": "1", "price": "10", "fee": "0", "timestamp": 1},
        {"id": "x", "symbol": "ETH", "side": "sell", "quantity": "1", "price": "11", "fee": "0", "timestamp": 2},
    ],
])
def test_fifo_rejects_incomplete_or_invalid_trade_sequences(trades):
    with pytest.raises(ValueError):
        calculate_fifo_cost_basis(trades)


def test_fifo_isolated_by_asset_symbol():
    result = calculate_fifo_cost_basis([
        {"id": "e1", "symbol": "ETH", "side": "buy", "quantity": "1", "price": "100", "fee": "0", "timestamp": 1},
        {"id": "b1", "symbol": "BTC", "side": "buy", "quantity": "1", "price": "1000", "fee": "0", "timestamp": 1},
    ])
    assert set(result["symbols"]) == {"BTC", "ETH"}
    assert Decimal(result["symbols"]["ETH"]["open_cost_basis"]) == Decimal("100")
    assert Decimal(result["symbols"]["BTC"]["open_cost_basis"]) == Decimal("1000")



def test_mark_to_market_values_open_fifo_lots_with_fresh_price():
    from cost_basis import value_open_positions

    basis = calculate_fifo_cost_basis([
        {"id": "b1", "symbol": "ETH", "side": "buy", "quantity": "2", "price": "100", "fee": "2", "timestamp": 1},
        {"id": "s1", "symbol": "ETH", "side": "sell", "quantity": "1", "price": "130", "fee": "1", "timestamp": 2},
    ])
    valued = value_open_positions(
        basis, {"ETH": {"price": "120", "as_of": 1000}}, now=1030, max_age_seconds=60
    )
    eth = valued["positions"]["ETH"]
    assert eth["status"] == "estimated"
    assert Decimal(eth["market_value"]) == Decimal("120")
    assert Decimal(eth["unrealized_pnl"]) == Decimal("19")
    assert eth["price_age_seconds"] == 30
    assert valued["account_wide_pnl_available"] is False


@pytest.mark.parametrize(
    "quote,now,reason",
    [
        (None, 100, "missing_mark_price"),
        ({"price": "100", "as_of": 1}, 100, "stale_mark_price"),
        ({"price": "100", "as_of": 101}, 100, "future_dated_price"),
        ({"price": "100", "as_of": "bad"}, 100, "invalid_price_timestamp"),
    ],
)
def test_mark_to_market_fails_closed_for_missing_or_stale_prices(quote, now, reason):
    from cost_basis import value_open_positions

    basis = calculate_fifo_cost_basis([
        {"id": "b1", "symbol": "ETH", "side": "buy", "quantity": "1", "price": "100", "fee": "0", "timestamp": 1},
    ])
    prices = {} if quote is None else {"ETH": quote}
    result = value_open_positions(basis, prices, now=now, max_age_seconds=30)
    assert result["positions"]["ETH"]["status"] == "unavailable"
    assert result["positions"]["ETH"]["reason"] == reason


def test_mark_to_market_handles_closed_positions_without_price():
    from cost_basis import value_open_positions

    basis = calculate_fifo_cost_basis([
        {"id": "b1", "symbol": "ETH", "side": "buy", "quantity": "1", "price": "100", "fee": "0", "timestamp": 1},
        {"id": "s1", "symbol": "ETH", "side": "sell", "quantity": "1", "price": "110", "fee": "0", "timestamp": 2},
    ])
    result = value_open_positions(basis, {}, now=100, max_age_seconds=30)
    assert result["positions"]["ETH"]["status"] == "no_open_position"
    assert Decimal(result["positions"]["ETH"]["unrealized_pnl"]) == Decimal("0")
