"""Read-only Owner Master Console data access."""
from __future__ import annotations
import sqlite3, time, json
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

class OwnerConsole:
    def __init__(self, database_path: str):
        self.database_path = str(database_path)

    def _connect(self):
        path = Path(self.database_path).resolve()
        if not path.exists():
            raise RuntimeError("Owner console database is not available.")
        connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=5)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only=ON")
        return connection

    @staticmethod
    def _table_exists(db, table: str) -> bool:
        return db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone() is not None

    def overview(self, *, partner_status: dict[str, Any], readiness: dict[str, Any]) -> dict:
        with self._connect() as db:
            def count(table: str, where: str = "", args: tuple = ()) -> int:
                if not self._table_exists(db, table):
                    return 0
                return int(db.execute(f"SELECT COUNT(*) FROM {table} {where}", args).fetchone()[0])
            order_status = {}
            if self._table_exists(db, "customer_order_requests"):
                for row in db.execute("SELECT status, COUNT(*) AS n FROM customer_order_requests GROUP BY status"):
                    order_status[str(row["status"])] = int(row["n"])
            wallet_assets = {}
            if self._table_exists(db, "wallet_ledger_entries"):
                rows = db.execute("SELECT asset, account_code, direction, amount FROM wallet_ledger_entries ORDER BY entry_id").fetchall()
                for row in rows:
                    if row["account_code"] != "customer_available":
                        continue
                    try:
                        amount = Decimal(str(row["amount"]))
                        if not amount.is_finite() or amount < 0:
                            continue
                    except (InvalidOperation, ValueError):
                        continue
                    asset = str(row["asset"])
                    wallet_assets[asset] = wallet_assets.get(asset, Decimal("0")) + (amount if row["direction"] == "credit" else -amount)
            statuses_requiring_reconciliation = {"UNKNOWN", "SUBMITTING", "RECONCILIATION_REQUIRED", "PARTIALLY_RECONCILED"}
            pending = sum(n for status, n in order_status.items() if status.upper() in statuses_requiring_reconciliation)
            return {
                "generated_at": int(time.time()), "mode": "READ_ONLY_OWNER_OVERVIEW",
                "live_customer_order_routing_enabled": False, "partner": partner_status, "readiness": readiness,
                "customers_with_connected_exchange": count("customer_exchange_connections", "WHERE status='connected'"),
                "customer_order_requests": {"total": sum(order_status.values()), "by_status": order_status, "requiring_reconciliation": pending},
                "wallet_ledger": {
                    "journals": count("wallet_journals"), "entries": count("wallet_ledger_entries"),
                    "customer_available_net_by_asset": {a: format(v, "f") for a, v in sorted(wallet_assets.items())},
                    "classification": "internal_ledger_records_not_proof_of_custodied_reserves"
                },
                "paper_trading": {"orders": count("paper_orders"), "trades": count("paper_trades"), "accounts": count("paper_accounts")},
                "warnings": [
                    "This console is operational reporting, not a bank or custodian balance confirmation.",
                    "Do not enable live customer trading, deposits or withdrawals before partner, legal, compliance, security and reconciliation approvals."
                ]
            }

    def recent_orders(self, limit: int = 100) -> dict:
        limit = max(1, min(int(limit), 250))
        with self._connect() as db:
            if not self._table_exists(db, "customer_order_requests"):
                return {"orders": [], "count": 0}
            rows = db.execute("""SELECT user_id, idempotency_key, exchange_client_order_id, status,
                response_json, created_at, updated_at FROM customer_order_requests
                ORDER BY created_at DESC LIMIT ?""", (limit,)).fetchall()
            orders = []
            for row in rows:
                payload = json.loads(row["response_json"]) if row["response_json"] else None
                allowed = {"exchange", "exchange_order_id", "client_order_id", "symbol", "side", "order_type",
                    "amount", "price", "status", "filled", "remaining", "cost", "submitted_at",
                    "last_synced_at", "execution_status", "idempotent_replay"}
                safe = {k: v for k, v in payload.items() if k in allowed} if isinstance(payload, dict) else None
                orders.append({"customer_reference": str(row["user_id"]), "idempotency_key": row["idempotency_key"],
                    "client_order_id": row["exchange_client_order_id"], "request_status": row["status"],
                    "order": safe, "created_at": row["created_at"], "updated_at": row["updated_at"]})
            return {"orders": orders, "count": len(orders)}

    def ledger_integrity(self) -> dict:
        issues, checked = [], 0
        with self._connect() as db:
            if not self._table_exists(db, "wallet_journals") or not self._table_exists(db, "wallet_ledger_entries"):
                return {"status": "not_initialized", "healthy": False, "checked_journals": 0, "issues": []}
            journals = db.execute("SELECT journal_id, user_id, asset FROM wallet_journals ORDER BY created_at").fetchall()
            known_ids = {r["journal_id"] for r in journals}
            for journal in journals:
                checked += 1
                entries = db.execute("SELECT user_id, asset, direction, amount FROM wallet_ledger_entries WHERE journal_id=?", (journal["journal_id"],)).fetchall()
                debit, credit = Decimal("0"), Decimal("0")
                invalid = len(entries) < 2
                for entry in entries:
                    try:
                        amount = Decimal(str(entry["amount"]))
                    except (InvalidOperation, ValueError):
                        invalid = True
                        continue
                    if entry["user_id"] != journal["user_id"] or entry["asset"] != journal["asset"] or not amount.is_finite() or amount <= 0:
                        invalid = True
                    elif entry["direction"] == "debit":
                        debit += amount
                    elif entry["direction"] == "credit":
                        credit += amount
                    else:
                        invalid = True
                if invalid:
                    issues.append({"journal_id": journal["journal_id"], "reason": "invalid_or_incomplete_postings"})
                elif debit != credit:
                    issues.append({"journal_id": journal["journal_id"], "reason": "unbalanced"})
            for row in db.execute("SELECT DISTINCT journal_id FROM wallet_ledger_entries"):
                if row["journal_id"] not in known_ids:
                    issues.append({"journal_id": row["journal_id"], "reason": "orphaned_postings"})
            balances = {}
            for row in db.execute("SELECT user_id, asset, direction, amount FROM wallet_ledger_entries WHERE account_code='customer_available'"):
                try:
                    amount = Decimal(str(row["amount"]))
                except (InvalidOperation, ValueError):
                    issues.append({"reason": "invalid_customer_balance_amount"})
                    continue
                key = (row["user_id"], row["asset"])
                balances[key] = balances.get(key, Decimal("0")) + (amount if row["direction"] == "credit" else -amount)
            for (uid, asset), balance in balances.items():
                if balance < 0:
                    issues.append({"customer_reference": str(uid), "asset": asset, "reason": "negative_available_balance"})
        return {"status": "healthy" if not issues else "review_required", "healthy": not issues,
            "checked_journals": checked, "issue_count": len(issues), "issues": issues[:100],
            "checked_at": int(time.time()), "scope": "internal_wallet_ledger_only"}


    def risk_alerts(self, *, readiness: dict[str, Any], reconciliation: dict[str, Any]) -> dict:
        """Build read-only risk alerts from existing, observable platform evidence."""
        alerts: list[dict[str, Any]] = []
        now = int(time.time())

        ledger = self.ledger_integrity()
        if ledger.get("status") == "not_initialized":
            alerts.append({
                "id": "ledger_not_initialized", "severity": "high", "category": "ledger",
                "title": "Wallet ledger integrity scan unavailable",
                "description": "Required internal ledger tables are not initialized; integrity cannot be confirmed.",
                "evidence": {"status": ledger.get("status")},
            })
        elif not ledger.get("healthy", False):
            reasons: dict[str, int] = {}
            for issue in ledger.get("issues", []):
                reason = str(issue.get("reason", "unknown"))
                reasons[reason] = reasons.get(reason, 0) + 1
            alerts.append({
                "id": "ledger_integrity_issues", "severity": "critical", "category": "ledger",
                "title": "Wallet ledger integrity issues detected",
                "description": "Internal journal postings require investigation before any financial reliance.",
                "evidence": {"issue_count": ledger.get("issue_count", len(ledger.get("issues", []))),
                             "reasons": dict(sorted(reasons.items()))},
            })

        checks = readiness.get("checks", {}) if isinstance(readiness, dict) else {}
        failed_checks = sorted(str(k) for k, value in checks.items() if value is False)
        if failed_checks:
            alerts.append({
                "id": "platform_readiness_failed", "severity": "high", "category": "platform",
                "title": "Platform readiness checks are failing",
                "description": "One or more required infrastructure or release checks are not passing.",
                "evidence": {"failed_checks": failed_checks},
            })

        if self.database_path != ":memory:":
            with self._connect() as db:
                if self._table_exists(db, "customer_order_requests"):
                    statuses: dict[str, int] = {}
                    for row in db.execute(
                        "SELECT status, COUNT(*) AS n FROM customer_order_requests GROUP BY status"
                    ):
                        statuses[str(row["status"]).upper()] = int(row["n"])
                    pending_statuses = {"UNKNOWN", "SUBMITTING", "RECONCILIATION_REQUIRED", "PARTIALLY_RECONCILED"}
                    pending = {k: v for k, v in sorted(statuses.items()) if k in pending_statuses and v > 0}
                    pending_count = sum(pending.values())
                    if pending_count:
                        alerts.append({
                            "id": "customer_orders_pending_reconciliation", "severity": "high",
                            "category": "execution",
                            "title": "Customer orders require reconciliation",
                            "description": "Some customer order requests remain in an uncertain or partially reconciled state.",
                            "evidence": {"count": pending_count, "by_status": pending},
                        })

        mismatch_count = int(reconciliation.get("mismatched_orders", 0) or 0)
        unmatched_count = int(reconciliation.get("unmatched_provider_orders", 0) or 0)
        if mismatch_count:
            alerts.append({
                "id": "execution_reconciliation_mismatch", "severity": "critical",
                "category": "execution", "title": "Provider execution mismatches detected",
                "description": "Provider fill totals differ from local order snapshots or local order data is invalid.",
                "evidence": {"mismatched_orders": mismatch_count},
            })
        if unmatched_count:
            alerts.append({
                "id": "unmatched_provider_executions", "severity": "high",
                "category": "execution", "title": "Unmatched provider executions detected",
                "description": "Provider execution records do not currently map to a known local customer order.",
                "evidence": {"unmatched_provider_orders": unmatched_count},
            })

        rank = {"critical": 0, "high": 1, "medium": 2, "low": 3}
        alerts.sort(key=lambda item: (rank[item["severity"]], item["category"], item["id"]))
        highest = alerts[0]["severity"] if alerts else "none"
        return {
            "generated_at": now,
            "mode": "READ_ONLY_RISK_ALERTS",
            "risk_level": highest,
            "alert_count": len(alerts),
            "critical_count": sum(1 for item in alerts if item["severity"] == "critical"),
            "high_count": sum(1 for item in alerts if item["severity"] == "high"),
            "alerts": alerts,
            "monitoring_scope": [
                "internal wallet ledger integrity",
                "configured platform readiness checks",
                "customer order reconciliation statuses",
                "normalized provider execution reconciliation",
            ],
            "limitations": [
                "No login-attempt, device, IP reputation or API-key activity telemetry is currently connected to this report.",
                "An empty alert list means no issue was found in the monitored sources; it is not proof that the platform is secure.",
                "Alerts are generated from read-only evidence and do not automatically freeze accounts, cancel orders or move funds.",
            ],
        }

    def financial_summary(self, days: int = 30) -> dict:
        """Read-only financial summary of simulated paper trading and fee postings."""
        days = max(1, min(int(days), 90))
        now = int(time.time())
        cutoff_seconds = now - days * 86400
        cutoff_ns = cutoff_seconds * 1_000_000_000
        fees_by_asset: dict[str, Decimal] = {}
        daily_fees: dict[str, dict[str, Decimal]] = {}
        volume_by_quote: dict[str, Decimal] = {}
        daily_volume: dict[str, dict[str, Decimal]] = {}
        transaction_kinds: dict[str, int] = {}
        trade_count = 0
        with self._connect() as db:
            tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if "ledger_transactions" in tables and "ledger_entries" in tables:
                for row in db.execute(
                    "SELECT kind, COUNT(*) AS n FROM ledger_transactions WHERE created_at >= ? GROUP BY kind",
                    (cutoff_ns,),
                ):
                    transaction_kinds[str(row["kind"])] = int(row["n"])
                rows = db.execute("""
                    SELECT t.created_at, e.account_code, e.asset, e.debit, e.credit
                    FROM ledger_transactions t
                    JOIN ledger_entries e ON e.transaction_id=t.id
                    WHERE t.created_at >= ?
                    ORDER BY t.created_at
                """, (cutoff_ns,)).fetchall()
                for row in rows:
                    if row["account_code"] == "platform:fees":
                        amount = Decimal(str(row["credit"])) - Decimal(str(row["debit"]))
                        if amount:
                            asset = str(row["asset"])
                            fees_by_asset[asset] = fees_by_asset.get(asset, Decimal("0")) + amount
                            day = time.strftime("%Y-%m-%d", time.gmtime(int(row["created_at"]) // 1_000_000_000))
                            daily_fees.setdefault(day, {})
                            daily_fees[day][asset] = daily_fees[day].get(asset, Decimal("0")) + amount
            if "paper_trades" in tables:
                columns = {r["name"] for r in db.execute("PRAGMA table_info(paper_trades)")}
                needed = {"symbol", "price", "quantity", "created_at"}
                if needed.issubset(columns):
                    rows = db.execute(
                        "SELECT symbol, price, quantity, created_at FROM paper_trades WHERE created_at >= ? ORDER BY created_at",
                        (cutoff_ns,),
                    ).fetchall()
                    trade_count = len(rows)
                    for row in rows:
                        symbol = str(row["symbol"])
                        if "/" not in symbol:
                            continue
                        base, quote = symbol.split("/", 1)
                        try:
                            notional = Decimal(str(row["price"])) * Decimal(str(row["quantity"]))
                        except (InvalidOperation, ValueError):
                            continue
                        if not notional.is_finite() or notional < 0:
                            continue
                        volume_by_quote[quote] = volume_by_quote.get(quote, Decimal("0")) + notional
                        day = time.strftime("%Y-%m-%d", time.gmtime(int(row["created_at"]) // 1_000_000_000))
                        daily_volume.setdefault(day, {})
                        daily_volume[day][quote] = daily_volume[day].get(quote, Decimal("0")) + notional
        days_out = sorted(set(daily_fees) | set(daily_volume))
        return {
            "generated_at": now,
            "mode": "READ_ONLY_SIMULATED_PAPER_FINANCIALS",
            "period_days": days,
            "period_start": cutoff_seconds,
            "paper_trade_count": trade_count,
            "paper_volume_by_quote_asset": {a: format(v, "f") for a, v in sorted(volume_by_quote.items())},
            "simulated_fee_revenue_by_asset": {a: format(v, "f") for a, v in sorted(fees_by_asset.items())},
            "ledger_transaction_count_by_kind": dict(sorted(transaction_kinds.items())),
            "daily": [{
                "date": day,
                "paper_volume_by_quote_asset": {a: format(v, "f") for a, v in sorted(daily_volume.get(day, {}).items())},
                "simulated_fee_revenue_by_asset": {a: format(v, "f") for a, v in sorted(daily_fees.get(day, {}).items())},
            } for day in days_out],
            "classification": "SIMULATED_PAPER_TRADING_ONLY_NOT_REAL_CUSTOMER_REVENUE_OR_CUSTODIED_ASSETS",
        }
