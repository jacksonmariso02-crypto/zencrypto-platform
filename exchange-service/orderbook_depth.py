"""Deterministic Decimal-based order-book aggregation and depth metrics."""
from decimal import Decimal, InvalidOperation


def build_depth(symbol, bids, asks, limit=50, source="paper_engine"):
    if not isinstance(limit, int) or not 1 <= limit <= 100:
        raise ValueError("limit must be between 1 and 100.")

    def aggregate(rows, reverse):
        grouped = {}
        for price_raw, quantity_raw in rows:
            try:
                price = Decimal(str(price_raw))
                quantity = Decimal(str(quantity_raw))
            except (InvalidOperation, TypeError, ValueError) as exc:
                raise ValueError("Order-book values must be valid decimals.") from exc
            if not price.is_finite() or not quantity.is_finite() or price <= 0 or quantity <= 0:
                continue
            grouped[price] = grouped.get(price, Decimal("0")) + quantity
        ordered = sorted(grouped.items(), key=lambda item: item[0], reverse=reverse)[:limit]
        cumulative = Decimal("0")
        levels = []
        for price, quantity in ordered:
            cumulative += quantity
            levels.append({
                "price": str(price),
                "quantity": str(quantity),
                "cumulative_quantity": str(cumulative),
                "notional": str(price * quantity),
                "cumulative_notional": str(
                    sum((p * q for p, q in ordered[:len(levels)+1]), Decimal("0"))
                ),
            })
        return levels

    bid_levels = aggregate(bids, True)
    ask_levels = aggregate(asks, False)
    best_bid = Decimal(bid_levels[0]["price"]) if bid_levels else None
    best_ask = Decimal(ask_levels[0]["price"]) if ask_levels else None
    spread = best_ask - best_bid if best_bid is not None and best_ask is not None else None
    mid = (best_ask + best_bid) / Decimal("2") if spread is not None else None
    bid_qty = sum((Decimal(level["quantity"]) for level in bid_levels), Decimal("0"))
    ask_qty = sum((Decimal(level["quantity"]) for level in ask_levels), Decimal("0"))
    total_qty = bid_qty + ask_qty
    imbalance = (bid_qty - ask_qty) / total_qty if total_qty else None

    return {
        "source": source,
        "symbol": symbol,
        "bids": bid_levels,
        "asks": ask_levels,
        "metrics": {
            "best_bid": str(best_bid) if best_bid is not None else None,
            "best_ask": str(best_ask) if best_ask is not None else None,
            "spread": str(spread) if spread is not None else None,
            "mid_price": str(mid) if mid is not None else None,
            "spread_bps": str(spread / mid * Decimal("10000")) if spread is not None and mid else None,
            "bid_depth_quantity": str(bid_qty),
            "ask_depth_quantity": str(ask_qty),
            "bid_depth_notional": str(sum((Decimal(x["notional"]) for x in bid_levels), Decimal("0"))),
            "ask_depth_notional": str(sum((Decimal(x["notional"]) for x in ask_levels), Decimal("0"))),
            "imbalance": str(imbalance) if imbalance is not None else None,
        },
    }
