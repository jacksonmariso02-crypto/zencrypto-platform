"""SQLite-backed, deterministic paper-only price/time matching engine.

This module never connects to an external exchange and never moves real assets.
"""
import sqlite3
from contextlib import closing
import time
import uuid
from decimal import Decimal, InvalidOperation
from pathlib import Path
import re


class IdempotencyConflict(ValueError):
    pass


class PaperMatchingEngine:
    def __init__(self, db_path: str):
        self.db_path = str(db_path)
        if self.db_path != ":memory:":
            Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self):
        connection = sqlite3.connect(self.db_path, timeout=15, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout = 15000")
        return connection

    def _initialize(self):
        with closing(self._connect()) as db:
            db.execute("PRAGMA journal_mode = WAL")
            db.executescript("""
                CREATE TABLE IF NOT EXISTS paper_orders (
                    id TEXT PRIMARY KEY,
                    client_order_id TEXT NOT NULL UNIQUE,
                    symbol TEXT NOT NULL,
                    side TEXT NOT NULL CHECK(side IN ('buy','sell')),
                    price TEXT NOT NULL,
                    quantity TEXT NOT NULL,
                    remaining TEXT NOT NULL,
                    status TEXT NOT NULL CHECK(status IN ('open','partially_filled','filled','cancelled')),
                    created_at INTEGER NOT NULL,
                    updated_at INTEGER NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_paper_orders_book
                    ON paper_orders(symbol, side, status, created_at);
                CREATE TABLE IF NOT EXISTS paper_trades (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    symbol TEXT NOT NULL,
                    maker_order_id TEXT NOT NULL,
                    taker_order_id TEXT NOT NULL,
                    price TEXT NOT NULL,
                    quantity TEXT NOT NULL,
                    created_at INTEGER NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_paper_trades_symbol
                    ON paper_trades(symbol, id DESC);
            """)

    @staticmethod
    def _decimal(value, field):
        try:
            result = Decimal(str(value))
        except (InvalidOperation, ValueError, TypeError) as exc:
            raise ValueError(f"{field} must be a valid decimal.") from exc
        if not result.is_finite() or result <= 0:
            raise ValueError(f"{field} must be finite and greater than zero.")
        return result

    @staticmethod
    def _serialize(row):
        if row is None:
            return None
        result = dict(row)
        for key in ("price", "quantity", "remaining"):
            if key in result:
                result[key] = str(result[key])
        return result

    def place_limit_order(self, symbol, side, quantity, price, client_order_id):
        symbol = str(symbol).strip().upper()
        if not re.fullmatch(r"[A-Z0-9]{2,15}/[A-Z0-9]{2,15}", symbol):
            raise ValueError("Use a symbol such as BTC/USDT.")
        if side not in ("buy", "sell"):
            raise ValueError("side must be buy or sell.")
        client_order_id = str(client_order_id).strip()
        if not 8 <= len(client_order_id) <= 64 or not re.fullmatch(r"[A-Za-z0-9._:-]+", client_order_id):
            raise ValueError("client_order_id must be 8-64 safe characters.")
        qty = self._decimal(quantity, "quantity")
        limit_price = self._decimal(price, "price")
        now = time.time_ns()
        order_id = str(uuid.uuid4())
        opposite_side = "sell" if side == "buy" else "buy"

        with closing(self._connect()) as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                prior = db.execute(
                    "SELECT * FROM paper_orders WHERE client_order_id = ?",
                    (client_order_id,),
                ).fetchone()
                if prior:
                    same_request = (
                        prior["symbol"] == symbol
                        and prior["side"] == side
                        and Decimal(prior["quantity"]) == qty
                        and Decimal(prior["price"]) == limit_price
                    )
                    if not same_request:
                        raise IdempotencyConflict("client_order_id was already used for a different order.")
                    db.commit()
                    return self._serialize(prior)

                db.execute(
                    """INSERT INTO paper_orders
                    (id, client_order_id, symbol, side, price, quantity, remaining, status, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, 'open', ?, ?)""",
                    (order_id, client_order_id, symbol, side, str(limit_price), str(qty), str(qty), now, now),
                )

                candidates = db.execute(
                    """SELECT * FROM paper_orders
                    WHERE symbol = ? AND side = ? AND status IN ('open','partially_filled')
                    """,
                    (symbol, opposite_side),
                ).fetchall()
                if side == "buy":
                    candidates = sorted(
                        (r for r in candidates if Decimal(r["price"]) <= limit_price),
                        key=lambda r: (Decimal(r["price"]), r["created_at"], r["id"]),
                    )
                else:
                    candidates = sorted(
                        (r for r in candidates if Decimal(r["price"]) >= limit_price),
                        key=lambda r: (-Decimal(r["price"]), r["created_at"], r["id"]),
                    )

                taker_remaining = qty
                for maker in candidates:
                    if taker_remaining <= 0:
                        break
                    maker_remaining = Decimal(maker["remaining"])
                    fill_qty = min(taker_remaining, maker_remaining)
                    if fill_qty <= 0:
                        continue
                    # The resting maker's price is the execution price.
                    trade_price = Decimal(maker["price"])
                    maker_left = maker_remaining - fill_qty
                    taker_remaining -= fill_qty
                    maker_status = "filled" if maker_left == 0 else "partially_filled"
                    db.execute(
                        "UPDATE paper_orders SET remaining=?, status=?, updated_at=? WHERE id=?",
                        (str(maker_left), maker_status, now, maker["id"]),
                    )
                    db.execute(
                        """INSERT INTO paper_trades
                        (symbol, maker_order_id, taker_order_id, price, quantity, created_at)
                        VALUES (?, ?, ?, ?, ?, ?)""",
                        (symbol, maker["id"], order_id, str(trade_price), str(fill_qty), now),
                    )

                taker_status = "filled" if taker_remaining == 0 else (
                    "partially_filled" if taker_remaining < qty else "open"
                )
                db.execute(
                    "UPDATE paper_orders SET remaining=?, status=?, updated_at=? WHERE id=?",
                    (str(taker_remaining), taker_status, now, order_id),
                )
                result = db.execute("SELECT * FROM paper_orders WHERE id=?", (order_id,)).fetchone()
                db.commit()
                return self._serialize(result)
            except Exception:
                db.rollback()
                raise

    def get_order(self, order_id):
        with closing(self._connect()) as db:
            return self._serialize(db.execute(
                "SELECT * FROM paper_orders WHERE id=?", (order_id,)
            ).fetchone())

    def list_orders(self, symbol=None, limit=100):
        with closing(self._connect()) as db:
            if symbol:
                rows = db.execute(
                    "SELECT * FROM paper_orders WHERE symbol=? ORDER BY created_at DESC LIMIT ?",
                    (symbol.upper(), limit),
                ).fetchall()
            else:
                rows = db.execute(
                    "SELECT * FROM paper_orders ORDER BY created_at DESC LIMIT ?", (limit,)
                ).fetchall()
            return [self._serialize(row) for row in rows]

    def cancel_order(self, order_id):
        now = time.time_ns()
        with closing(self._connect()) as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                row = db.execute("SELECT * FROM paper_orders WHERE id=?", (order_id,)).fetchone()
                if row is None:
                    raise KeyError("Paper order not found.")
                if row["status"] not in ("open", "partially_filled"):
                    raise ValueError("Only open or partially filled orders can be cancelled.")
                db.execute(
                    "UPDATE paper_orders SET status='cancelled', updated_at=? WHERE id=?",
                    (now, order_id),
                )
                result = db.execute("SELECT * FROM paper_orders WHERE id=?", (order_id,)).fetchone()
                db.commit()
                return self._serialize(result)
            except Exception:
                db.rollback()
                raise

    def orderbook(self, symbol, limit=50):
        symbol = symbol.strip().upper()
        with closing(self._connect()) as db:
            rows = db.execute(
                """SELECT * FROM paper_orders
                WHERE symbol=? AND status IN ('open','partially_filled')""",
                (symbol,),
            ).fetchall()
        bids = sorted(
            (r for r in rows if r["side"] == "buy"),
            key=lambda r: (-Decimal(r["price"]), r["created_at"], r["id"]),
        )[:limit]
        asks = sorted(
            (r for r in rows if r["side"] == "sell"),
            key=lambda r: (Decimal(r["price"]), r["created_at"], r["id"]),
        )[:limit]
        def levels(orders):
            grouped = {}
            for order in orders:
                p = str(order["price"])
                grouped[p] = grouped.get(p, Decimal("0")) + Decimal(order["remaining"])
            return [{"price": price, "quantity": str(qty)} for price, qty in grouped.items()]
        return {"symbol": symbol, "bids": levels(bids), "asks": levels(asks)}

    def list_trades(self, symbol=None, limit=100):
        with closing(self._connect()) as db:
            if symbol:
                rows = db.execute(
                    "SELECT * FROM paper_trades WHERE symbol=? ORDER BY id DESC LIMIT ?",
                    (symbol.upper(), limit),
                ).fetchall()
            else:
                rows = db.execute(
                    "SELECT * FROM paper_trades ORDER BY id DESC LIMIT ?", (limit,)
                ).fetchall()
            return [dict(row) for row in rows]
