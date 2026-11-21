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

from paper_ledger import InsufficientPaperFunds, PaperLedger
from market_rules import MarketRuleRegistry
from orderbook_depth import build_depth


class IdempotencyConflict(ValueError):
    pass


class PaperMatchingEngine:
    def __init__(self, db_path: str, maker_fee_bps=0, taker_fee_bps=0, market_rules=None):
        self.db_path = str(db_path)
        self.market_rules = market_rules or MarketRuleRegistry()
        self.maker_fee_bps = self._fee_bps(maker_fee_bps, "maker_fee_bps")
        self.taker_fee_bps = self._fee_bps(taker_fee_bps, "taker_fee_bps")
        if self.db_path != ":memory:":
            Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    @staticmethod
    def _fee_bps(value, field):
        try:
            rate = int(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{field} must be an integer number of basis points.") from exc
        if rate < 0 or rate > 1000:
            raise ValueError(f"{field} must be between 0 and 1000 basis points.")
        return rate

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
            columns = {row["name"] for row in db.execute("PRAGMA table_info(paper_orders)")}
            if "account_id" not in columns:
                db.execute("ALTER TABLE paper_orders ADD COLUMN account_id TEXT")
            db.execute(
                "CREATE INDEX IF NOT EXISTS idx_paper_orders_account_book "
                "ON paper_orders(symbol, account_id, side, status, created_at)"
            )
            db.execute(
                "CREATE INDEX IF NOT EXISTS idx_paper_trades_symbol_time "
                "ON paper_trades(symbol, created_at, id)"
            )
            trade_columns = {row["name"] for row in db.execute("PRAGMA table_info(paper_trades)")}
            for column, definition in (
                ("maker_fee_amount", "TEXT NOT NULL DEFAULT '0'"),
                ("maker_fee_asset", "TEXT"),
                ("taker_fee_amount", "TEXT NOT NULL DEFAULT '0'"),
                ("taker_fee_asset", "TEXT"),
            ):
                if column not in trade_columns:
                    db.execute(f"ALTER TABLE paper_trades ADD COLUMN {column} {definition}")

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

    def place_limit_order(self, symbol, side, quantity, price, client_order_id, account_id=None):
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
        self.market_rules.get(symbol).validate(limit_price, qty)
        if account_id is not None:
            account_id = PaperLedger._account_id(account_id)
        base_asset, quote_asset = symbol.split("/", 1)
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
                        and prior["account_id"] == account_id
                    )
                    if not same_request:
                        raise IdempotencyConflict("client_order_id was already used for a different order.")
                    db.commit()
                    return self._serialize(prior)

                if account_id is not None:
                    if not db.execute(
                        "SELECT 1 FROM paper_accounts WHERE account_id=?", (account_id,)
                    ).fetchone():
                        raise KeyError("Paper account not found.")
                    reserve_asset = quote_asset if side == "buy" else base_asset
                    reserve_amount = qty * limit_price if side == "buy" else qty
                    available_code = f"user:{account_id}:available"
                    locked_code = f"user:{account_id}:locked"
                    if PaperLedger._balance(db, available_code, reserve_asset) < reserve_amount:
                        raise InsufficientPaperFunds(
                            f"Insufficient {reserve_asset} available balance for this order."
                        )
                    PaperLedger._post(
                        db, f"paper-order-reserve:{order_id}", "order_reserve",
                        f"Reserve funds for paper order {order_id}",
                        [
                            (available_code, reserve_asset, "debit", reserve_amount),
                            (locked_code, reserve_asset, "credit", reserve_amount),
                        ],
                    )

                db.execute(
                    """INSERT INTO paper_orders
                    (id, client_order_id, symbol, side, price, quantity, remaining, status, created_at, updated_at, account_id)
                    VALUES (?, ?, ?, ?, ?, ?, ?, 'open', ?, ?, ?)""",
                    (order_id, client_order_id, symbol, side, str(limit_price), str(qty), str(qty), now, now, account_id),
                )

                if account_id is None:
                    candidates = db.execute(
                        """SELECT * FROM paper_orders
                        WHERE symbol = ? AND side = ? AND account_id IS NULL
                          AND status IN ('open','partially_filled')""",
                        (symbol, opposite_side),
                    ).fetchall()
                else:
                    candidates = db.execute(
                        """SELECT * FROM paper_orders
                        WHERE symbol = ? AND side = ? AND account_id IS NOT NULL
                          AND status IN ('open','partially_filled')""",
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
                    # Never allow two orders belonging to the same paper account
                    # to trade against each other. Keep the resting order intact;
                    # the incoming order may match other eligible counterparties.
                    if account_id is not None and maker["account_id"] == account_id:
                        continue
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
                    trade_cursor = db.execute(
                        """INSERT INTO paper_trades
                        (symbol, maker_order_id, taker_order_id, price, quantity, created_at)
                        VALUES (?, ?, ?, ?, ?, ?)""",
                        (symbol, maker["id"], order_id, str(trade_price), str(fill_qty), now),
                    )
                    if account_id is not None:
                        taker_order = {
                            "id": order_id, "side": side, "price": str(limit_price),
                            "account_id": account_id,
                        }
                        fees = self._settle_account_trade(
                            db, maker, taker_order, trade_price, fill_qty, trade_cursor.lastrowid
                        )
                        db.execute(
                            """UPDATE paper_trades SET maker_fee_amount=?, maker_fee_asset=?,
                               taker_fee_amount=?, taker_fee_asset=? WHERE id=?""",
                            (
                                fees["maker_fee_amount"], fees["maker_fee_asset"],
                                fees["taker_fee_amount"], fees["taker_fee_asset"],
                                trade_cursor.lastrowid,
                            ),
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

    def _settle_account_trade(self, db, maker, taker, trade_price, quantity, trade_id):
        """Settle both sides and maker/taker fees in the trade's SQLite transaction."""
        buy_order = taker if taker["side"] == "buy" else maker
        sell_order = maker if taker["side"] == "buy" else taker
        buyer_id = buy_order["account_id"]
        seller_id = sell_order["account_id"]
        if not buyer_id or not seller_id:
            raise ValueError("Account-backed orders can only match other account-backed orders.")

        base_asset, quote_asset = maker["symbol"].split("/", 1)
        buyer_limit = Decimal(buy_order["price"])
        execution_value = trade_price * quantity
        buyer_reserved = buyer_limit * quantity
        refund = buyer_reserved - execution_value
        if refund < 0:
            raise ValueError("Execution price exceeds the buyer's reserved limit price.")

        buyer_is_maker = buy_order["id"] == maker["id"]
        seller_is_maker = sell_order["id"] == maker["id"]
        buyer_rate = self.maker_fee_bps if buyer_is_maker else self.taker_fee_bps
        seller_rate = self.maker_fee_bps if seller_is_maker else self.taker_fee_bps
        buyer_fee = quantity * Decimal(buyer_rate) / Decimal("10000")
        seller_fee = execution_value * Decimal(seller_rate) / Decimal("10000")
        buyer_received = quantity - buyer_fee
        seller_received = execution_value - seller_fee
        if buyer_received < 0 or seller_received < 0:
            raise ValueError("Configured paper trading fee exceeds trade proceeds.")

        postings = [
            (f"user:{buyer_id}:locked", quote_asset, "debit", buyer_reserved),
            (f"user:{seller_id}:available", quote_asset, "credit", seller_received),
            (f"user:{seller_id}:locked", base_asset, "debit", quantity),
            (f"user:{buyer_id}:available", base_asset, "credit", buyer_received),
        ]
        if seller_fee > 0:
            postings.append(("platform:fees", quote_asset, "credit", seller_fee))
        if buyer_fee > 0:
            postings.append(("platform:fees", base_asset, "credit", buyer_fee))
        if refund > 0:
            postings.append((f"user:{buyer_id}:available", quote_asset, "credit", refund))

        PaperLedger._post(
            db, f"paper-trade-settlement:{trade_id}", "trade_settlement",
            f"Settle paper trade {trade_id} including maker/taker fees", postings,
        )
        maker_fee = buyer_fee if buyer_is_maker else seller_fee
        maker_asset = base_asset if buyer_is_maker else quote_asset
        taker_fee = buyer_fee if not buyer_is_maker else seller_fee
        taker_asset = base_asset if not buyer_is_maker else quote_asset
        return {
            "maker_fee_amount": str(maker_fee),
            "maker_fee_asset": maker_asset if maker_fee > 0 else None,
            "taker_fee_amount": str(taker_fee),
            "taker_fee_asset": taker_asset if taker_fee > 0 else None,
        }

    def get_order(self, order_id):
        with closing(self._connect()) as db:
            return self._serialize(db.execute(
                "SELECT * FROM paper_orders WHERE id=?", (order_id,)
            ).fetchone())

    def list_orders(self, symbol=None, limit=100, account_id=None):
        with closing(self._connect()) as db:
            clauses = []
            params = []
            if symbol:
                clauses.append("symbol=?")
                params.append(symbol.upper())
            if account_id is not None:
                clauses.append("account_id=?")
                params.append(PaperLedger._account_id(account_id))
            where = " WHERE " + " AND ".join(clauses) if clauses else ""
            params.append(limit)
            rows = db.execute(
                f"SELECT * FROM paper_orders{where} ORDER BY created_at DESC LIMIT ?",
                params,
            ).fetchall()
            return [self._serialize(row) for row in rows]

    def cancel_order(self, order_id, account_id=None):
        now = time.time_ns()
        with closing(self._connect()) as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                row = db.execute("SELECT * FROM paper_orders WHERE id=?", (order_id,)).fetchone()
                if row is None or (account_id is not None and row["account_id"] != PaperLedger._account_id(account_id)):
                    raise KeyError("Paper order not found.")
                if row["status"] not in ("open", "partially_filled"):
                    raise ValueError("Only open or partially filled orders can be cancelled.")
                if row["account_id"] is not None:
                    base_asset, quote_asset = row["symbol"].split("/", 1)
                    remaining = Decimal(row["remaining"])
                    reserve_asset = quote_asset if row["side"] == "buy" else base_asset
                    reserve_amount = remaining * Decimal(row["price"]) if row["side"] == "buy" else remaining
                    if reserve_amount > 0:
                        PaperLedger._post(
                            db, f"paper-order-cancel:{order_id}", "order_release",
                            f"Release remaining reserve for paper order {order_id}",
                            [
                                (f"user:{row['account_id']}:locked", reserve_asset, "debit", reserve_amount),
                                (f"user:{row['account_id']}:available", reserve_asset, "credit", reserve_amount),
                            ],
                        )
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

    def orderbook_depth(self, symbol, limit=50):
        normalized = str(symbol).strip().upper()
        if not re.fullmatch(r"[A-Z0-9]{2,15}/[A-Z0-9]{2,15}", normalized):
            raise ValueError("Use a symbol such as BTC/USDT.")
        if not isinstance(limit, int) or not 1 <= limit <= 100:
            raise ValueError("limit must be between 1 and 100.")
        with closing(self._connect()) as db:
            rows = db.execute(
                """SELECT side, price, remaining
                   FROM paper_orders
                   WHERE symbol=? AND status IN ('open','partially_filled')
                     AND CAST(remaining AS NUMERIC) > 0""",
                (normalized,),
            ).fetchall()
        bids = [(row["price"], row["remaining"]) for row in rows if row["side"] == "buy"]
        asks = [(row["price"], row["remaining"]) for row in rows if row["side"] == "sell"]
        return build_depth(normalized, bids, asks, limit=limit, source="zencrypto_paper")

    def list_candles(self, symbol, timeframe="1h", limit=100, since=None):
        """Aggregate durable paper fills into OHLCV candles; timestamps are UTC milliseconds."""
        intervals = {
            "1m": 60_000, "5m": 300_000, "15m": 900_000,
            "30m": 1_800_000, "1h": 3_600_000,
            "4h": 14_400_000, "1d": 86_400_000,
        }
        if timeframe not in intervals:
            raise ValueError("Unsupported timeframe. Use 1m, 5m, 15m, 30m, 1h, 4h or 1d.")
        if not isinstance(limit, int) or not 1 <= limit <= 1000:
            raise ValueError("limit must be between 1 and 1000.")
        normalized = str(symbol).strip().upper()
        if not re.fullmatch(r"[A-Z0-9]{2,15}/[A-Z0-9]{2,15}", normalized):
            raise ValueError("Use a symbol such as BTC/USDT.")
        interval_ms = intervals[timeframe]
        interval_ns = interval_ms * 1_000_000
        params = [interval_ns, interval_ns, normalized]
        since_clause = ""
        if since is not None:
            try:
                since_ms = int(since)
            except (TypeError, ValueError) as exc:
                raise ValueError("since must be a UTC Unix timestamp in milliseconds.") from exc
            if since_ms < 0:
                raise ValueError("since must be a non-negative UTC Unix timestamp in milliseconds.")
            since_clause = " AND created_at >= ?"
            params.append(since_ms * 1_000_000)
        params.append(limit)
        query = f"""
            WITH bucketed AS (
                SELECT id, symbol, price, quantity, created_at,
                       (created_at / ?) * ? AS bucket
                FROM paper_trades
                WHERE symbol = ?{since_clause}
            ),
            recent AS (
                SELECT DISTINCT bucket FROM bucketed
                ORDER BY bucket DESC LIMIT ?
            )
            SELECT b.bucket, b.price, b.quantity, b.created_at, b.id
            FROM bucketed b JOIN recent r ON r.bucket = b.bucket
            ORDER BY b.bucket ASC, b.created_at ASC, b.id ASC
        """
        with closing(self._connect()) as db:
            rows = db.execute(query, params).fetchall()

        candles = []
        current_bucket = None
        current = None
        for row in rows:
            bucket = int(row["bucket"])
            price = Decimal(row["price"])
            quantity = Decimal(row["quantity"])
            if bucket != current_bucket:
                if current is not None:
                    candles.append(current)
                current_bucket = bucket
                current = {
                    "timestamp": bucket // 1_000_000,
                    "open": price, "high": price, "low": price,
                    "close": price, "volume": quantity,
                }
            else:
                current["high"] = max(current["high"], price)
                current["low"] = min(current["low"], price)
                current["close"] = price
                current["volume"] += quantity
        if current is not None:
            candles.append(current)
        return [
            {key: (value if key == "timestamp" else str(value))
             for key, value in candle.items()}
            for candle in candles
        ]

    def latest_trade_id(self, symbol=None):
        with closing(self._connect()) as db:
            if symbol:
                row = db.execute(
                    "SELECT COALESCE(MAX(id), 0) AS latest_id FROM paper_trades WHERE symbol=?",
                    (symbol.upper(),),
                ).fetchone()
            else:
                row = db.execute(
                    "SELECT COALESCE(MAX(id), 0) AS latest_id FROM paper_trades"
                ).fetchone()
            return int(row["latest_id"])

    def list_trades_after(self, after_id=0, symbol=None, limit=500):
        if not isinstance(after_id, int) or after_id < 0:
            raise ValueError("after_id must be a non-negative integer.")
        if not isinstance(limit, int) or not 1 <= limit <= 500:
            raise ValueError("limit must be between 1 and 500.")
        with closing(self._connect()) as db:
            if symbol:
                rows = db.execute(
                    """SELECT id, symbol, maker_order_id, taker_order_id,
                              price, quantity, created_at
                       FROM paper_trades WHERE symbol=? AND id>?
                       ORDER BY id ASC LIMIT ?""",
                    (symbol.upper(), after_id, limit),
                ).fetchall()
            else:
                rows = db.execute(
                    """SELECT id, symbol, maker_order_id, taker_order_id,
                              price, quantity, created_at
                       FROM paper_trades WHERE id>?
                       ORDER BY id ASC LIMIT ?""",
                    (after_id, limit),
                ).fetchall()
            return [dict(row) for row in rows]

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
