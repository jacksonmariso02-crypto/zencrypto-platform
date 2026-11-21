"""Deterministic per-symbol rules for paper-market order validation."""
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation


@dataclass(frozen=True)
class MarketRule:
    symbol: str
    tick_size: Decimal
    step_size: Decimal
    min_quantity: Decimal
    min_notional: Decimal
    price_precision: int
    quantity_precision: int

    @classmethod
    def build(cls, symbol, tick_size, step_size, min_quantity, min_notional):
        values = {}
        for field, raw in (
            ("tick_size", tick_size),
            ("step_size", step_size),
            ("min_quantity", min_quantity),
            ("min_notional", min_notional),
        ):
            try:
                value = Decimal(str(raw))
            except (InvalidOperation, TypeError, ValueError) as exc:
                raise ValueError(f"{field} must be a valid decimal.") from exc
            if not value.is_finite() or value <= 0:
                raise ValueError(f"{field} must be finite and greater than zero.")
            values[field] = value
        return cls(
            symbol=symbol.upper(),
            **values,
            price_precision=max(0, -values["tick_size"].normalize().as_tuple().exponent),
            quantity_precision=max(0, -values["step_size"].normalize().as_tuple().exponent),
        )

    def validate(self, price, quantity):
        price = Decimal(str(price))
        quantity = Decimal(str(quantity))
        if price % self.tick_size != 0:
            raise ValueError(
                f"Price for {self.symbol} must be a multiple of {self.tick_size}."
            )
        if quantity % self.step_size != 0:
            raise ValueError(
                f"Quantity for {self.symbol} must be a multiple of {self.step_size}."
            )
        if quantity < self.min_quantity:
            raise ValueError(
                f"Quantity for {self.symbol} must be at least {self.min_quantity}."
            )
        notional = price * quantity
        if notional < self.min_notional:
            raise ValueError(
                f"Order notional for {self.symbol} must be at least {self.min_notional}."
            )


DEFAULT_MARKET_RULES = {
    "BTC/USDT": MarketRule.build("BTC/USDT", "0.01", "0.000001", "0.00001", "5"),
    "ETH/USDT": MarketRule.build("ETH/USDT", "0.01", "0.00001", "0.0001", "5"),
    "SOL/USDT": MarketRule.build("SOL/USDT", "0.001", "0.001", "0.01", "5"),
    "BNB/USDT": MarketRule.build("BNB/USDT", "0.01", "0.001", "0.001", "5"),
    "XRP/USDT": MarketRule.build("XRP/USDT", "0.0001", "0.1", "1", "5"),
    "DOGE/USDT": MarketRule.build("DOGE/USDT", "0.00001", "1", "1", "5"),
}


class MarketRuleRegistry:
    def __init__(self, rules=None):
        self._rules = dict(rules or DEFAULT_MARKET_RULES)

    def get(self, symbol):
        normalized = str(symbol).strip().upper()
        rule = self._rules.get(normalized)
        if rule is None:
            raise ValueError(f"Trading is not enabled for market {normalized}.")
        return rule

    def list_markets(self):
        return [
            {
                "symbol": rule.symbol,
                "base_asset": rule.symbol.split("/", 1)[0],
                "quote_asset": rule.symbol.split("/", 1)[1],
                "tick_size": str(rule.tick_size),
                "step_size": str(rule.step_size),
                "min_quantity": str(rule.min_quantity),
                "min_notional": str(rule.min_notional),
                "price_precision": rule.price_precision,
                "quantity_precision": rule.quantity_precision,
            }
            for rule in sorted(self._rules.values(), key=lambda item: item.symbol)
        ]
