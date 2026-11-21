from decimal import Decimal

import pytest

from market_rules import MarketRule, MarketRuleRegistry


def test_default_market_metadata_is_explicit_and_sorted():
    markets = MarketRuleRegistry().list_markets()
    assert [market["symbol"] for market in markets] == [
        "BNB/USDT", "BTC/USDT", "DOGE/USDT", "ETH/USDT", "SOL/USDT", "XRP/USDT"
    ]
    btc = next(item for item in markets if item["symbol"] == "BTC/USDT")
    assert btc == {
        "symbol": "BTC/USDT",
        "base_asset": "BTC",
        "quote_asset": "USDT",
        "tick_size": "0.01",
        "step_size": "0.000001",
        "min_quantity": "0.00001",
        "min_notional": "5",
        "price_precision": 2,
        "quantity_precision": 6,
    }


def test_valid_price_and_quantity_multiples_are_accepted():
    rule = MarketRule.build("BTC/USDT", "0.01", "0.000001", "0.00001", "5")
    rule.validate(Decimal("50000.00"), Decimal("0.0001"))
    rule.validate(Decimal("50000.0100"), Decimal("0.000100"))


@pytest.mark.parametrize(
    ("price", "quantity", "message"),
    [
        ("50000.001", "0.0001", "Price"),
        ("50000", "0.0001001", "Quantity"),
        ("50000", "0.000001", "at least"),
        ("1000", "0.0001", "notional"),
    ],
)
def test_invalid_market_values_are_rejected(price, quantity, message):
    rule = MarketRule.build("BTC/USDT", "0.01", "0.000001", "0.00001", "5")
    with pytest.raises(ValueError, match=message):
        rule.validate(Decimal(price), Decimal(quantity))


def test_market_registry_rejects_disabled_symbols():
    with pytest.raises(ValueError, match="not enabled"):
        MarketRuleRegistry().get("ABC/USDT")


@pytest.mark.parametrize(
    ("tick", "step", "minimum", "notional"),
    [
        ("0", "0.1", "0.1", "5"),
        ("0.01", "-0.1", "0.1", "5"),
        ("0.01", "0.1", "NaN", "5"),
        ("0.01", "0.1", "0.1", "Infinity"),
    ],
)
def test_market_rule_configuration_requires_positive_finite_values(tick, step, minimum, notional):
    with pytest.raises(ValueError):
        MarketRule.build("TEST/USDT", tick, step, minimum, notional)
