"""Immutable, balanced double-entry ledger for paper-trading accounts only.

No real deposits, withdrawals, custody, or external payment rails are supported.
"""
import re
import sqlite3
import time
import uuid
from contextlib import closing
from decimal import Decimal, InvalidOperation
from pathlib import Path


class LedgerIdempotencyConflict(ValueError):
    pass


class InsufficientPaperFunds(ValueError):
    pass


class PaperLedger:
    def __init__(self, db_path: str):
        self.db_path = str(db_path)
        if self.db_path != ":memory:":
            Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self):
        db = sqlite3.connect(self.db_path, timeout=15, isolation_level=None)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA busy_timeout = 15000")
        return db

    def _initialize(self):
        with closing(self._connect()) as db:
            db.execute("PRAGMA journal_mode = WAL")
            db.executescript("""
                CREATE TABLE IF NOT EXISTS paper_accounts (
                    account_id TEXT PRIMARY KEY,
                    created_at INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS ledger_transactions (
                    id TEXT PRIMARY KEY,
                    idempotency_key TEXT NOT NULL UNIQUE,
                    kind TEXT NOT NULL,
                    memo TEXT NOT NULL,
                    created_at INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS ledger_entries (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    transaction_id TEXT NOT NULL REFERENCES ledger_transactions(id),
                    account_code TEXT NOT NULL,
                    asset TEXT NOT NULL,
                    debit TEXT NOT NULL,
                    credit TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS daily_asset_snapshots (
                    snapshot_date TEXT NOT NULL,
                    account_id TEXT NOT NULL REFERENCES paper_accounts(account_id),
                    asset TEXT NOT NULL,
                    available TEXT NOT NULL,
                    locked TEXT NOT NULL,
                    created_at INTEGER NOT NULL,
                    PRIMARY KEY (snapshot_date, account_id, asset)
                );
                CREATE INDEX IF NOT EXISTS idx_daily_snapshots_account_date
                    ON daily_asset_snapshots(account_id, snapshot_date DESC);
                CREATE INDEX IF NOT EXISTS idx_ledger_entries_account_asset
                    ON ledger_entries(account_code, asset, id);
                CREATE INDEX IF NOT EXISTS idx_ledger_entries_transaction
                    ON ledger_entries(transaction_id, asset);
            """)

    @staticmethod
    def _amount(value):
        try:
            amount = Decimal(str(value))
        except (InvalidOperation, ValueError, TypeError) as exc:
            raise ValueError("amount must be a valid decimal.") from exc
        if not amount.is_finite() or amount <= 0:
            raise ValueError("amount must be finite and greater than zero.")
        return amount

    @staticmethod
    def _account_id(account_id):
        value = str(account_id).strip()
        if not re.fullmatch(r"[A-Za-z0-9_-]{3,40}", value):
            raise ValueError("account_id must be 3-40 safe characters.")
        return value

    @staticmethod
    def _asset(asset):
        value = str(asset).strip().upper()
        if not re.fullmatch(r"[A-Z0-9]{2,12}", value):
            raise ValueError("asset must be a supported ticker such as USDT or BTC.")
        return value

    @staticmethod
    def _key(key):
        value = str(key).strip()
        if not re.fullmatch(r"[A-Za-z0-9._:-]{8,80}", value):
            raise ValueError("idempotency_key must be 8-80 safe characters.")
        return value

    @staticmethod
    def _balance(db, account_code, asset):
        rows = db.execute(
            "SELECT debit, credit FROM ledger_entries WHERE account_code=? AND asset=?",
            (account_code, asset),
        ).fetchall()
        return sum(
            (Decimal(row["credit"]) - Decimal(row["debit"]) for row in rows),
            Decimal("0"),
        )

    @staticmethod
    def _post(db, key, kind, memo, postings):
        """Post balanced debit/credit lines. Each asset must balance independently."""
        totals = {}
        for account_code, asset, side, amount in postings:
            if side not in ("debit", "credit") or amount <= 0:
                raise ValueError("Invalid ledger posting.")
            totals.setdefault(asset, {"debit": Decimal("0"), "credit": Decimal("0")})
            totals[asset][side] += amount
        if any(v["debit"] != v["credit"] for v in totals.values()):
            raise ValueError("Ledger transaction is not balanced for every asset.")

        prior = db.execute(
            "SELECT * FROM ledger_transactions WHERE idempotency_key=?", (key,)
        ).fetchone()
        if prior:
            existing = db.execute(
                """SELECT account_code, asset, debit, credit FROM ledger_entries
                   WHERE transaction_id=? ORDER BY id""",
                (prior["id"],),
            ).fetchall()
            expected = [
                (a, asset, str(amount) if side == "debit" else "0",
                 str(amount) if side == "credit" else "0")
                for a, asset, side, amount in postings
            ]
            actual = [(r["account_code"], r["asset"], r["debit"], r["credit"]) for r in existing]
            if prior["kind"] != kind or actual != expected:
                raise LedgerIdempotencyConflict(
                    "idempotency_key was already used for a different ledger transaction."
                )
            return prior["id"]

        transaction_id = str(uuid.uuid4())
        db.execute(
            "INSERT INTO ledger_transactions(id,idempotency_key,kind,memo,created_at) VALUES(?,?,?,?,?)",
            (transaction_id, key, kind, memo[:200], time.time_ns()),
        )
        db.executemany(
            """INSERT INTO ledger_entries(transaction_id,account_code,asset,debit,credit)
               VALUES(?,?,?,?,?)""",
            [
                (transaction_id, account, asset,
                 str(amount) if side == "debit" else "0",
                 str(amount) if side == "credit" else "0")
                for account, asset, side, amount in postings
            ],
        )
        return transaction_id

    def create_account(self, account_id):
        account_id = self._account_id(account_id)
        with closing(self._connect()) as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                db.execute(
                    "INSERT OR IGNORE INTO paper_accounts(account_id,created_at) VALUES(?,?)",
                    (account_id, time.time_ns()),
                )
                db.commit()
            except Exception:
                db.rollback()
                raise
        return {"account_id": account_id, "status": "active"}

    def credit_paper_funds(self, account_id, asset, amount, idempotency_key):
        account_id = self._account_id(account_id)
        asset = self._asset(asset)
        amount = self._amount(amount)
        key = self._key(idempotency_key)
        user_account = f"user:{account_id}:available"
        with closing(self._connect()) as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                if not db.execute(
                    "SELECT 1 FROM paper_accounts WHERE account_id=?", (account_id,)
                ).fetchone():
                    raise KeyError("Paper account not found.")
                txid = self._post(db, key, "paper_credit", "Simulated paper funds",
                    [
                        ("system:paper_funding", asset, "debit", amount),
                        (user_account, asset, "credit", amount),
                    ])
                db.commit()
                return {"transaction_id": txid, "account_id": account_id,
                        "asset": asset, "amount": str(amount), "kind": "paper_credit"}
            except Exception:
                db.rollback()
                raise

    def transfer(self, account_id, asset, amount, from_bucket, to_bucket, idempotency_key, memo=""):
        account_id = self._account_id(account_id)
        asset = self._asset(asset)
        amount = self._amount(amount)
        key = self._key(idempotency_key)
        if from_bucket not in ("available", "locked") or to_bucket not in ("available", "locked") or from_bucket == to_bucket:
            raise ValueError("Buckets must be different available/locked values.")
        source = f"user:{account_id}:{from_bucket}"
        destination = f"user:{account_id}:{to_bucket}"
        with closing(self._connect()) as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                if not db.execute("SELECT 1 FROM paper_accounts WHERE account_id=?", (account_id,)).fetchone():
                    raise KeyError("Paper account not found.")
                postings = [(source, asset, "debit", amount), (destination, asset, "credit", amount)]
                prior = db.execute(
                    "SELECT 1 FROM ledger_transactions WHERE idempotency_key=?", (key,)
                ).fetchone()
                if prior:
                    txid = self._post(db, key, "bucket_transfer", memo, postings)
                else:
                    if self._balance(db, source, asset) < amount:
                        raise InsufficientPaperFunds(f"Insufficient {asset} {from_bucket} balance.")
                    txid = self._post(db, key, "bucket_transfer", memo, postings)
                db.commit()
                return {"transaction_id": txid, "account_id": account_id,
                        "asset": asset, "amount": str(amount),
                        "from": from_bucket, "to": to_bucket}
            except Exception:
                db.rollback()
                raise

    def get_balances(self, account_id):
        account_id = self._account_id(account_id)
        available_code = f"user:{account_id}:available"
        locked_code = f"user:{account_id}:locked"
        with closing(self._connect()) as db:
            if not db.execute("SELECT 1 FROM paper_accounts WHERE account_id=?", (account_id,)).fetchone():
                raise KeyError("Paper account not found.")
            rows = db.execute(
                """SELECT asset, account_code, debit, credit FROM ledger_entries
                   WHERE account_code IN (?, ?) ORDER BY id""",
                (available_code, locked_code),
            ).fetchall()
            totals = {}
            for row in rows:
                item = totals.setdefault(row["asset"], {
                    "available": Decimal("0"), "locked": Decimal("0")
                })
                bucket = "available" if row["account_code"] == available_code else "locked"
                item[bucket] += Decimal(row["credit"]) - Decimal(row["debit"])
            return [
                {"asset": asset, "available": str(values["available"]),
                 "locked": str(values["locked"])}
                for asset, values in sorted(totals.items())
                if values["available"] or values["locked"]
            ]

    def get_fee_balances(self):
        """Return simulated platform fee revenue by asset; never includes user funds."""
        with closing(self._connect()) as db:
            rows = db.execute(
                """SELECT asset, debit, credit FROM ledger_entries
                   WHERE account_code='platform:fees' ORDER BY id"""
            ).fetchall()
            totals = {}
            for row in rows:
                totals[row["asset"]] = totals.get(row["asset"], Decimal("0")) + (
                    Decimal(row["credit"]) - Decimal(row["debit"])
                )
            return [
                {"asset": asset, "amount": str(amount)}
                for asset, amount in sorted(totals.items()) if amount
            ]

    def list_entries(self, account_id, limit=100):
        account_id = self._account_id(account_id)
        if not 1 <= limit <= 500:
            raise ValueError("limit must be between 1 and 500.")
        with closing(self._connect()) as db:
            if not db.execute("SELECT 1 FROM paper_accounts WHERE account_id=?", (account_id,)).fetchone():
                raise KeyError("Paper account not found.")
            rows = db.execute(
                """SELECT t.id AS transaction_id,t.kind,t.memo,t.created_at,
                          e.account_code,e.asset,e.debit,e.credit
                   FROM ledger_transactions t JOIN ledger_entries e ON e.transaction_id=t.id
                   WHERE e.account_code IN (?, ?) ORDER BY e.id DESC LIMIT ?""",
                (f"user:{account_id}:available", f"user:{account_id}:locked", limit * 2),
            ).fetchall()
            return [dict(row) for row in rows]


    def audit_integrity(self):
        """Read-only reconciliation of double-entry balance and user bucket solvency."""
        with closing(self._connect()) as db:
            quick = db.execute("PRAGMA quick_check").fetchone()
            integrity_ok = bool(quick and quick[0] == "ok")
            foreign_key_errors = db.execute("PRAGMA foreign_key_check").fetchall()

            transaction_rows = db.execute(
                """
                SELECT t.id AS transaction_id, e.asset,
                       SUM(CAST(e.debit AS NUMERIC)) AS debits,
                       SUM(CAST(e.credit AS NUMERIC)) AS credits,
                       COUNT(e.id) AS entry_count
                FROM ledger_transactions t
                LEFT JOIN ledger_entries e ON e.transaction_id=t.id
                GROUP BY t.id, e.asset
                """
            ).fetchall()
            unbalanced_transactions = []
            empty_transactions = []
            # Use Decimal for exact comparisons; SQL aggregation is used only to group rows.
            for row in transaction_rows:
                if row["asset"] is None or row["entry_count"] == 0:
                    empty_transactions.append(row["transaction_id"])
                    continue
                entries = db.execute(
                    """SELECT debit, credit FROM ledger_entries
                       WHERE transaction_id=? AND asset=?""",
                    (row["transaction_id"], row["asset"]),
                ).fetchall()
                debit_total = sum((Decimal(x["debit"]) for x in entries), Decimal("0"))
                credit_total = sum((Decimal(x["credit"]) for x in entries), Decimal("0"))
                if debit_total != credit_total:
                    unbalanced_transactions.append({
                        "transaction_id": row["transaction_id"],
                        "asset": row["asset"],
                    })

            negative_buckets = []
            rows = db.execute(
                """SELECT account_code, asset, debit, credit FROM ledger_entries
                   WHERE account_code LIKE 'user:%:available'
                      OR account_code LIKE 'user:%:locked'
                   ORDER BY id"""
            ).fetchall()
            balances = {}
            for row in rows:
                key = (row["account_code"], row["asset"])
                balances[key] = balances.get(key, Decimal("0")) + (
                    Decimal(row["credit"]) - Decimal(row["debit"])
                )
            for (account_code, asset), amount in balances.items():
                if amount < 0:
                    negative_buckets.append({
                        "account_code": account_code,
                        "asset": asset,
                        "amount": str(amount),
                    })

            return {
                "status": "ok" if (
                    integrity_ok and not foreign_key_errors
                    and not unbalanced_transactions and not empty_transactions
                    and not negative_buckets
                ) else "attention_required",
                "checks": {
                    "sqlite_integrity": integrity_ok,
                    "foreign_keys": not foreign_key_errors,
                    "transactions_balanced": not unbalanced_transactions,
                    "transactions_have_entries": not empty_transactions,
                    "user_buckets_nonnegative": not negative_buckets,
                },
                "counts": {
                    "transactions": db.execute(
                        "SELECT COUNT(*) FROM ledger_transactions"
                    ).fetchone()[0],
                    "entries": db.execute(
                        "SELECT COUNT(*) FROM ledger_entries"
                    ).fetchone()[0],
                    "accounts": db.execute(
                        "SELECT COUNT(*) FROM paper_accounts"
                    ).fetchone()[0],
                    "unbalanced_transactions": len(unbalanced_transactions),
                    "empty_transactions": len(empty_transactions),
                    "negative_buckets": len(negative_buckets),
                    "foreign_key_errors": len(foreign_key_errors),
                },
                "issues": {
                    "unbalanced_transactions": unbalanced_transactions[:50],
                    "empty_transactions": empty_transactions[:50],
                    "negative_buckets": negative_buckets[:50],
                },
            }


    @staticmethod
    def _snapshot_date(value=None):
        from datetime import date, datetime, timezone
        if value is None:
            return datetime.now(timezone.utc).date().isoformat()
        try:
            parsed = date.fromisoformat(str(value))
        except (TypeError, ValueError) as exc:
            raise ValueError("as_of_date must use YYYY-MM-DD format.") from exc
        if parsed.isoformat() != str(value):
            raise ValueError("as_of_date must use YYYY-MM-DD format.")
        if parsed != datetime.now(timezone.utc).date():
            raise ValueError("Snapshots can only be captured for the current UTC date; historical dates cannot be backfilled.")
        return parsed.isoformat()

    def capture_daily_snapshots(self, as_of_date=None):
        """Create immutable, idempotent end-of-day paper asset snapshots for all accounts."""
        snapshot_date = self._snapshot_date(as_of_date)
        with closing(self._connect()) as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                rows = db.execute(
                    """
                    SELECT DISTINCT a.account_id, e.asset
                    FROM paper_accounts a
                    JOIN ledger_entries e
                      ON e.account_code IN (
                         'user:' || a.account_id || ':available',
                         'user:' || a.account_id || ':locked'
                      )
                    GROUP BY a.account_id, e.asset
                    ORDER BY a.account_id, e.asset
                    """
                ).fetchall()
                balances = {}
                for row in rows:
                    balances.setdefault(row["account_id"], {})[row["asset"]] = {
                        "available": Decimal("0"), "locked": Decimal("0")
                    }
                entries = db.execute(
                    """
                    SELECT a.account_id, e.asset, e.account_code, e.debit, e.credit
                    FROM paper_accounts a
                    JOIN ledger_entries e
                      ON e.account_code IN (
                         'user:' || a.account_id || ':available',
                         'user:' || a.account_id || ':locked'
                      )
                    ORDER BY a.account_id, e.asset, e.id
                    """
                ).fetchall()
                for row in entries:
                    bucket = "available" if row["account_code"].endswith(":available") else "locked"
                    balances[row["account_id"]][row["asset"]][bucket] += (
                        Decimal(row["credit"]) - Decimal(row["debit"])
                    )
                created_at = time.time_ns()
                inserted = 0
                for account_id, assets in balances.items():
                    for asset, amounts in assets.items():
                        cursor = db.execute(
                            """
                            INSERT OR IGNORE INTO daily_asset_snapshots
                            (snapshot_date,account_id,asset,available,locked,created_at)
                            VALUES(?,?,?,?,?,?)
                            """,
                            (snapshot_date, account_id, asset,
                             str(amounts["available"]), str(amounts["locked"]), created_at),
                        )
                        inserted += cursor.rowcount
                db.commit()
                return {
                    "snapshot_date": snapshot_date,
                    "created": inserted,
                    "already_existed_or_empty": sum(len(x) for x in balances.values()) - inserted,
                    "status": "captured",
                }
            except Exception:
                db.rollback()
                raise

    def list_daily_snapshots(self, account_id=None, limit=100):
        if not 1 <= limit <= 500:
            raise ValueError("limit must be between 1 and 500.")
        if account_id is not None:
            account_id = self._account_id(account_id)
        with closing(self._connect()) as db:
            if account_id is not None and not db.execute(
                "SELECT 1 FROM paper_accounts WHERE account_id=?", (account_id,)
            ).fetchone():
                raise KeyError("Paper account not found.")
            if account_id is None:
                rows = db.execute(
                    """SELECT snapshot_date,account_id,asset,available,locked,created_at
                       FROM daily_asset_snapshots
                       ORDER BY snapshot_date DESC,account_id,asset LIMIT ?""",
                    (limit,),
                ).fetchall()
            else:
                rows = db.execute(
                    """SELECT snapshot_date,account_id,asset,available,locked,created_at
                       FROM daily_asset_snapshots WHERE account_id=?
                       ORDER BY snapshot_date DESC,asset LIMIT ?""",
                    (account_id, limit),
                ).fetchall()
            return [dict(row) for row in rows]


    def admin_finance_overview(self):
        """Read-only aggregate view of simulated user liabilities and platform fees."""
        with closing(self._connect()) as db:
            account_count = db.execute(
                "SELECT COUNT(*) FROM paper_accounts"
            ).fetchone()[0]
            transaction_count = db.execute(
                "SELECT COUNT(*) FROM ledger_transactions"
            ).fetchone()[0]
            entry_count = db.execute(
                "SELECT COUNT(*) FROM ledger_entries"
            ).fetchone()[0]
            snapshot_count = db.execute(
                "SELECT COUNT(*) FROM daily_asset_snapshots"
            ).fetchone()[0]
            latest_snapshot = db.execute(
                "SELECT MAX(snapshot_date) FROM daily_asset_snapshots"
            ).fetchone()[0]

            rows = db.execute(
                """
                SELECT asset, account_code, debit, credit
                FROM ledger_entries
                WHERE account_code LIKE 'user:%:available'
                   OR account_code LIKE 'user:%:locked'
                ORDER BY asset, account_code, id
                """
            ).fetchall()
            assets = {}
            for row in rows:
                item = assets.setdefault(row["asset"], {
                    "available": Decimal("0"), "locked": Decimal("0")
                })
                bucket = "available" if row["account_code"].endswith(":available") else "locked"
                item[bucket] += Decimal(row["credit"]) - Decimal(row["debit"])

            fee_rows = db.execute(
                """SELECT asset,debit,credit FROM ledger_entries
                   WHERE account_code='platform:fees' ORDER BY asset,id"""
            ).fetchall()
            fees = {}
            for row in fee_rows:
                fees[row["asset"]] = fees.get(row["asset"], Decimal("0")) + (
                    Decimal(row["credit"]) - Decimal(row["debit"])
                )

            by_kind = db.execute(
                """SELECT kind,COUNT(*) AS count FROM ledger_transactions
                   GROUP BY kind ORDER BY kind"""
            ).fetchall()

            return {
                "mode": "paper",
                "accounts": account_count,
                "ledger": {
                    "transactions": transaction_count,
                    "entries": entry_count,
                    "transactions_by_kind": {
                        row["kind"]: row["count"] for row in by_kind
                    },
                },
                "user_balances": [
                    {
                        "asset": asset,
                        "available": str(values["available"]),
                        "locked": str(values["locked"]),
                        "total": str(values["available"] + values["locked"]),
                    }
                    for asset, values in sorted(assets.items())
                ],
                "platform_fee_balances": [
                    {"asset": asset, "amount": str(amount)}
                    for asset, amount in sorted(fees.items()) if amount
                ],
                "snapshots": {
                    "count": snapshot_count,
                    "latest_utc_date": latest_snapshot,
                },
                "as_of_utc_ns": time.time_ns(),
                "source": "paper_double_entry_ledger",
            }
