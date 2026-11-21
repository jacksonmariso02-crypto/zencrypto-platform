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
        row = db.execute(
            """SELECT COALESCE(SUM(CAST(credit AS NUMERIC)), 0)
                     - COALESCE(SUM(CAST(debit AS NUMERIC)), 0) AS balance
               FROM ledger_entries WHERE account_code=? AND asset=?""",
            (account_code, asset),
        ).fetchone()
        return Decimal(str(row["balance"]))

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
