from collections import defaultdict, deque
from decimal import Decimal, InvalidOperation
from typing import Iterable


def _decimal(value, field, *, allow_zero=False):
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        raise ValueError(f"{field} must be a valid decimal number.")
    if not number.is_finite() or (number < 0 if allow_zero else number <= 0):
        raise ValueError(f"{field} must be {'non-negative' if allow_zero else 'positive'} and finite.")
    return number


def calculate_fifo_cost_basis(trades: Iterable[dict]):
    """Calculate FIFO realized P&L and remaining lots from a complete trade sequence.

    Each trade requires id, symbol, side, quantity, price, fee, and timestamp.
    Fees are denominated in the quote currency. Input must include every buy and
    sell affecting the position; deposits, withdrawals, transfers, and fee tokens
    must be reconciled by the caller before treating the result as account-wide P&L.
    """
    normalized = []
    seen_ids = set()
    for trade in trades:
        if not isinstance(trade, dict):
            raise ValueError("Each trade must be an object.")
        trade_id = str(trade.get("id", "")).strip()
        symbol = str(trade.get("symbol", "")).strip().upper()
        side = str(trade.get("side", "")).strip().lower()
        if not trade_id or trade_id in seen_ids:
            raise ValueError("Trade IDs must be present and unique.")
        seen_ids.add(trade_id)
        if not symbol or side not in {"buy", "sell"}:
            raise ValueError("Each trade requires a symbol and side buy or sell.")
        quantity = _decimal(trade.get("quantity"), "quantity")
        price = _decimal(trade.get("price"), "price")
        fee = _decimal(trade.get("fee", "0"), "fee", allow_zero=True)
        try:
            timestamp = int(trade.get("timestamp"))
        except (TypeError, ValueError, OverflowError):
            raise ValueError("timestamp must be an integer.")
        if timestamp < 0:
            raise ValueError("timestamp must be non-negative.")
        normalized.append({
            "id": trade_id, "symbol": symbol, "side": side,
            "quantity": quantity, "price": price, "fee": fee,
            "timestamp": timestamp,
        })

    normalized.sort(key=lambda item: (item["timestamp"], item["id"]))
    books = defaultdict(deque)
    realized = defaultdict(lambda: Decimal("0"))
    sold_quantity = defaultdict(lambda: Decimal("0"))
    bought_quantity = defaultdict(lambda: Decimal("0"))

    for trade in normalized:
        symbol = trade["symbol"]
        quantity = trade["quantity"]
        notional = quantity * trade["price"]
        if trade["side"] == "buy":
            unit_cost = (notional + trade["fee"]) / quantity
            books[symbol].append({
                "trade_id": trade["id"], "quantity": quantity,
                "remaining_quantity": quantity, "unit_cost": unit_cost,
                "remaining_cost": notional + trade["fee"],
                "acquired_at": trade["timestamp"],
            })
            bought_quantity[symbol] += quantity
            continue

        available = sum((lot["remaining_quantity"] for lot in books[symbol]), Decimal("0"))
        if quantity > available:
            raise ValueError(f"Sell quantity exceeds available FIFO lots for {symbol}.")
        proceeds_after_fee = notional - trade["fee"]
        if proceeds_after_fee < 0:
            raise ValueError("Sell fee cannot exceed gross proceeds.")
        remaining_to_match = quantity
        allocated_cost = Decimal("0")
        while remaining_to_match > 0:
            lot = books[symbol][0]
            matched = min(remaining_to_match, lot["remaining_quantity"])
            lot_cost = lot["unit_cost"] * matched
            allocated_cost += lot_cost
            lot["remaining_quantity"] -= matched
            lot["remaining_cost"] -= lot_cost
            remaining_to_match -= matched
            if lot["remaining_quantity"] == 0:
                books[symbol].popleft()
        realized[symbol] += proceeds_after_fee - allocated_cost
        sold_quantity[symbol] += quantity

    symbols = sorted(set(bought_quantity) | set(sold_quantity))
    summaries = {}
    for symbol in symbols:
        lots = list(books[symbol])
        open_quantity = sum((lot["remaining_quantity"] for lot in lots), Decimal("0"))
        open_cost = sum((lot["remaining_cost"] for lot in lots), Decimal("0"))
        summaries[symbol] = {
            "bought_quantity": str(bought_quantity[symbol]),
            "sold_quantity": str(sold_quantity[symbol]),
            "open_quantity": str(open_quantity),
            "open_cost_basis": str(open_cost),
            "average_open_cost": str(open_cost / open_quantity) if open_quantity else None,
            "realized_pnl": str(realized[symbol]),
            "open_lots": [{
                "source_trade_id": lot["trade_id"],
                "remaining_quantity": str(lot["remaining_quantity"]),
                "remaining_cost": str(lot["remaining_cost"]),
                "unit_cost": str(lot["unit_cost"]),
                "acquired_at": lot["acquired_at"],
            } for lot in lots],
        }
    return {"method": "FIFO", "quote_currency": "trade_quote_currency",
            "trade_count": len(normalized), "complete_sequence_required": True,
            "account_wide_pnl_available": False, "symbols": summaries}
