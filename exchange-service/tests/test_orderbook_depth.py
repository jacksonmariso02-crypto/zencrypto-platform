from decimal import Decimal

import pytest

from orderbook_depth import build_depth


def test_build_depth_aggregates_levels_and_calculates_cumulative_metrics():
    depth = build_depth(
        "BTC/USDT",
        bids=[["100", "1"], ["101", "2"], ["100", "3"], ["99", "4"]],
        asks=[["103", "1"], ["102", "2"], ["102", "3"]],
        limit=2,
        source="unit_test",
    )
    assert depth["source"] == "unit_test"
    assert depth["bids"] == [
        {"price": "101", "quantity": "2", "cumulative_quantity": "2",
         "notional": "202", "cumulative_notional": "202"},
        {"price": "100", "quantity": "4", "cumulative_quantity": "6",
         "notional": "400", "cumulative_notional": "602"},
    ]
    assert depth["asks"] == [
        {"price": "102", "quantity": "5", "cumulative_quantity": "5",
         "notional": "510", "cumulative_notional": "510"},
        {"price": "103", "quantity": "1", "cumulative_quantity": "6",
         "notional": "103", "cumulative_notional": "613"},
    ]
    metrics = depth["metrics"]
    assert metrics["best_bid"] == "101"
    assert metrics["best_ask"] == "102"
    assert metrics["spread"] == "1"
    assert metrics["mid_price"] == "101.5"
    assert metrics["spread_bps"] == str(Decimal(1) / Decimal("101.5") * Decimal(10000))
    assert metrics["bid_depth_quantity"] == "6"
    assert metrics["ask_depth_quantity"] == "6"
    assert metrics["imbalance"] == "0"


def test_build_depth_handles_one_sided_or_empty_books():
    depth = build_depth("BTC/USDT", bids=[["100", "1"]], asks=[], limit=5)
    assert depth["metrics"]["best_bid"] == "100"
    assert depth["metrics"]["best_ask"] is None
    assert depth["metrics"]["spread"] is None
    assert depth["metrics"]["imbalance"] == "1"

    empty = build_depth("ETH/USDT", [], [])
    assert empty["metrics"]["imbalance"] is None


@pytest.mark.parametrize("limit", [0, 101, -1])
def test_build_depth_rejects_invalid_limit(limit):
    with pytest.raises(ValueError, match="between 1 and 100"):
        build_depth("BTC/USDT", [], [], limit=limit)


def test_build_depth_ignores_non_positive_levels_and_rejects_invalid_decimal():
    depth = build_depth("BTC/USDT", [["0", "3"], ["100", "-1"], ["101", "2"]], [])
    assert len(depth["bids"]) == 1
    assert depth["bids"][0]["price"] == "101"
    with pytest.raises(ValueError, match="valid decimals"):
        build_depth("BTC/USDT", [["bad", "1"]], [])
