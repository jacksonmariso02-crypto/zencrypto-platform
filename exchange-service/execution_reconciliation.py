"""Durable normalized execution events and read-only reconciliation."""
from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any


class ExecutionEventConflict(ValueError):
    """A provider reused an event or execution ID with different details."""


def _decimal(value: Any, field: str, *, allow_zero: bool = False) -> Decimal:
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise ValueError(f"{field} must be a valid decimal") from exc
    if not result.is_finite() or result < 0 or (not allow_zero and result == 0):
        raise ValueError(f"{field} must be finite and {'non-negative' if allow_zero else 'positive'}")
    return result


class ExecutionReconciliation:
    """Stores normalized provider fills and compares them with local order snapshots.

    This service never posts ledger entries, moves funds, or assumes a provider
    event is settlement evidence. Provider signature verification is deliberately
    outside this class and must use the provider's documented scheme.
    """

    def __init__(self, database_path: str):
        self.database_path = str(database_path)
        if self.database_path != ":memory:":
            Path(self.database_path).parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            db.execute("""
                CREATE TABLE IF NOT EXISTS partner_execution_events (
                    provider TEXT NOT NULL,
                    provider_event_id TEXT NOT NULL,
                    partner_order_id TEXT NOT NULL,
                    execution_id TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    side TEXT NOT NULL CHECK(side IN ('BUY','SELL')),
                    quantity TEXT NOT NULL,
                    price TEXT NOT NULL,
                    fee_amount TEXT NOT NULL,
                    fee_asset TEXT NOT NULL,
                    executed_at_ms INTEGER NOT NULL,
                    payload_sha256 TEXT NOT NULL,
                    received_at INTEGER NOT NULL,
                    PRIMARY KEY(provider, provider_event_id),
                    UNIQUE(provider, execution_id)
                )
            """)
            db.execute("""CREATE INDEX IF NOT EXISTS idx_partner_exec_order
                ON partner_execution_events(provider, partner_order_id, executed_at_ms)""")

    def _connect(self):
        db = sqlite3.connect(self.database_path, timeout=15, isolation_level=None)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA busy_timeout=15000")
        return db

    @staticmethod
    def _normalize(event: dict[str, Any]) -> dict[str, Any]:
        required = ("provider", "provider_event_id", "partner_order_id", "execution_id",
                    "symbol", "side", "quantity", "price", "fee_amount", "fee_asset",
                    "executed_at_ms")
        if not isinstance(event, dict) or any(event.get(k) is None for k in required):
            raise ValueError("Execution event is missing required fields")
        provider = str(event["provider"]).strip().lower()
        event_id = str(event["provider_event_id"]).strip()
        order_id = str(event["partner_order_id"]).strip()
        execution_id = str(event["execution_id"]).strip()
        symbol = str(event["symbol"]).strip().upper()
        side = str(event["side"]).strip().upper()
        fee_asset = str(event["fee_asset"]).strip().upper()
        if not provider or len(provider) > 64 or not event_id or len(event_id) > 160:
            raise ValueError("Invalid provider or provider event ID")
        if not order_id or len(order_id) > 160 or not execution_id or len(execution_id) > 160:
            raise ValueError("Invalid partner order or execution ID")
        if not symbol or len(symbol) > 32 or side not in {"BUY", "SELL"}:
            raise ValueError("Invalid execution symbol or side")
        if not fee_asset or len(fee_asset) > 16:
            raise ValueError("Invalid fee asset")
        executed_at_ms = event["executed_at_ms"]
        if isinstance(executed_at_ms, bool) or not isinstance(executed_at_ms, int) or executed_at_ms <= 0:
            raise ValueError("executed_at_ms must be a positive integer")
        return {
            "provider": provider, "provider_event_id": event_id,
            "partner_order_id": order_id, "execution_id": execution_id,
            "symbol": symbol, "side": side,
            "quantity": format(_decimal(event["quantity"], "quantity"), "f"),
            "price": format(_decimal(event["price"], "price"), "f"),
            "fee_amount": format(_decimal(event["fee_amount"], "fee_amount", allow_zero=True), "f"),
            "fee_asset": fee_asset, "executed_at_ms": executed_at_ms,
        }

    def record_execution(self, event: dict[str, Any]) -> dict:
        normalized = self._normalize(event)
        canonical = json.dumps(normalized, sort_keys=True, separators=(",", ":"))
        digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            existing = db.execute(
                """SELECT provider_event_id, payload_sha256 FROM partner_execution_events
                   WHERE provider=? AND (provider_event_id=? OR execution_id=?)""",
                (normalized["provider"], normalized["provider_event_id"], normalized["execution_id"]),
            ).fetchone()
            if existing:
                if existing["payload_sha256"] != digest:
                    db.execute("ROLLBACK")
                    raise ExecutionEventConflict("Provider event or execution ID was reused with different details")
                db.execute("COMMIT")
                return {"recorded": False, "duplicate": True, "execution_id": normalized["execution_id"]}
            db.execute(
                """INSERT INTO partner_execution_events
                (provider, provider_event_id, partner_order_id, execution_id, symbol, side,
                 quantity, price, fee_amount, fee_asset, executed_at_ms, payload_sha256, received_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (normalized["provider"], normalized["provider_event_id"], normalized["partner_order_id"],
                 normalized["execution_id"], normalized["symbol"], normalized["side"],
                 normalized["quantity"], normalized["price"], normalized["fee_amount"],
                 normalized["fee_asset"], normalized["executed_at_ms"], digest, int(time.time())),
            )
            db.execute("COMMIT")
        return {"recorded": True, "duplicate": False, "execution_id": normalized["execution_id"]}

    def reconciliation_report(self, limit: int = 250) -> dict:
        limit = max(1, min(int(limit), 1000))
        with self._connect() as db:
            events = db.execute(
                """SELECT provider, provider_event_id, partner_order_id, execution_id, symbol,
                          side, quantity, price, fee_amount, fee_asset, executed_at_ms
                   FROM partner_execution_events ORDER BY executed_at_ms DESC LIMIT ?""",
                (limit,),
            ).fetchall()
            totals: dict[tuple[str, str], dict[str, Any]] = {}
            for row in events:
                key = (row["provider"], row["partner_order_id"])
                item = totals.setdefault(key, {
                    "provider": row["provider"], "partner_order_id": row["partner_order_id"],
                    "symbol": row["symbol"], "side": row["side"],
                    "fill_count": 0, "filled_quantity": Decimal("0"),
                    "gross_value": Decimal("0"), "fees_by_asset": {},
                    "last_execution_at_ms": 0,
                })
                item["fill_count"] += 1
                qty, price, fee = Decimal(row["quantity"]), Decimal(row["price"]), Decimal(row["fee_amount"])
                item["filled_quantity"] += qty
                item["gross_value"] += qty * price
                item["fees_by_asset"][row["fee_asset"]] = item["fees_by_asset"].get(row["fee_asset"], Decimal("0")) + fee
                item["last_execution_at_ms"] = max(item["last_execution_at_ms"], row["executed_at_ms"])

            local_orders = []
            if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='customer_order_requests'").fetchone():
                local_orders = db.execute(
                    """SELECT user_id, idempotency_key, status, response_json
                       FROM customer_order_requests ORDER BY updated_at DESC LIMIT 1000"""
                ).fetchall()
            local_by_key = {}
            for row in local_orders:
                try:
                    payload = json.loads(row["response_json"]) if row["response_json"] else None
                except (TypeError, json.JSONDecodeError):
                    payload = None
                if isinstance(payload, dict) and payload.get("exchange_order_id"):
                    provider = str(payload.get("exchange") or "").lower()
                    local_by_key[(provider, str(payload["exchange_order_id"]))] = (row, payload)

            matched = []
            unmatched = []
            for key, item in totals.items():
                local = local_by_key.get(key)
                if not local:
                    unmatched.append({
                        "provider": item["provider"], "partner_order_id": item["partner_order_id"],
                        "symbol": item["symbol"], "fill_count": item["fill_count"],
                        "filled_quantity": format(item["filled_quantity"], "f"),
                        "gross_value": format(item["gross_value"], "f"),
                        "fees_by_asset": {a: format(v, "f") for a, v in sorted(item["fees_by_asset"].items())},
                        "status": "unmatched_provider_execution",
                    })
                    continue
                row, payload = local
                local_filled = _decimal(payload.get("filled", "0"), "local filled", allow_zero=True)
                local_cost = _decimal(payload.get("cost", "0"), "local cost", allow_zero=True)
                quantity_delta = item["filled_quantity"] - local_filled
                cost_delta = item["gross_value"] - local_cost
                status = "matched"
                if quantity_delta != 0:
                    status = "quantity_mismatch"
                elif cost_delta != 0:
                    status = "gross_value_mismatch"
                matched.append({
                    "customer_reference": str(row["user_id"]),
                    "idempotency_key": row["idempotency_key"],
                    "provider": item["provider"], "partner_order_id": item["partner_order_id"],
                    "symbol": item["symbol"], "fill_count": item["fill_count"],
                    "provider_filled_quantity": format(item["filled_quantity"], "f"),
                    "local_filled_quantity": format(local_filled, "f"),
                    "quantity_delta": format(quantity_delta, "f"),
                    "provider_gross_value": format(item["gross_value"], "f"),
                    "local_reported_cost": format(local_cost, "f"),
                    "gross_value_delta": format(cost_delta, "f"),
                    "fees_by_asset": {a: format(v, "f") for a, v in sorted(item["fees_by_asset"].items())},
                    "status": status,
                })
            mismatch_count = sum(1 for row in matched if row["status"] != "matched")
            return {
                "mode": "READ_ONLY_EXECUTION_RECONCILIATION",
                "provider_events_scanned": len(events),
                "provider_orders_aggregated": len(totals),
                "matched_orders": len(matched),
                "mismatched_orders": mismatch_count,
                "unmatched_provider_orders": len(unmatched),
                "matched": matched[:limit],
                "unmatched": unmatched[:limit],
                "warnings": [
                    "Provider execution events must be authenticated using the provider's documented signature scheme before storage.",
                    "This comparison is not settlement confirmation and does not post customer ledger entries.",
                    "Gross value comparisons are exact decimal comparisons; provider-specific rounding rules may require an approved tolerance."
                ],
                "generated_at": int(time.time()),
            }
