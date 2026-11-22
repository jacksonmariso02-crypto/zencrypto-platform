"""ZenCrypto platform API. Legacy paper-trading routes are disabled by default."""
import os, sqlite3, secrets, hashlib, hmac, time, json, re
from decimal import Decimal, InvalidOperation, ROUND_DOWN
from contextlib import contextmanager
from typing import Literal
from fastapi import FastAPI, HTTPException, Header, Depends
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired

DB = os.getenv("DATABASE_PATH", "zencrypto.db")
PAPER_TRADING_ENABLED = os.getenv("PAPER_TRADING_ENABLED", "false").lower() == "true"
SUPPORTED_PAPER_ASSETS = frozenset({"BTC", "ETH", "SOL", "BNB", "XRP"})
SECRET = os.getenv("APP_SECRET")
if not SECRET:
    SECRET = secrets.token_urlsafe(48)
TOKEN = URLSafeTimedSerializer(SECRET, salt="zencrypto-session-v1")
bearer = HTTPBearer(auto_error=False)
app = FastAPI(title="ZenCrypto Platform API", version="1.1.0")

@app.middleware("http")
async def disable_legacy_paper_trading(request, call_next):
    path = request.url.path
    paper_only = (
        path == "/trade" or path.startswith("/trade/")
        or path in {"/account/balances", "/account/ledger", "/account/ledger/verify"}
        or path == "/sandbox" or path.startswith("/sandbox/")
    )
    if paper_only and not PAPER_TRADING_ENABLED:
        return JSONResponse(
            status_code=410,
            content={
                "detail": "Legacy simulated trading has been retired from the public application. Connect your own exchange account through the exchange connector."
            },
        )
    return await call_next(request)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[x.strip() for x in os.getenv("CORS_ORIGINS", "http://localhost:5173,https://jacksonmariso02-crypto.github.io").split(",") if x.strip()],
    allow_credentials=False, allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "Idempotency-Key"]
)

@contextmanager
def db():
    c = sqlite3.connect(DB, timeout=20, isolation_level=None)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys=ON")
    c.execute("PRAGMA busy_timeout=20000")
    try:
        yield c
    finally:
        c.close()

def audit(c, uid, event, detail):
    c.execute("INSERT INTO audit(actor_id,event,detail,created_at) VALUES(?,?,?,?)",
              (uid, event, json.dumps(detail, separators=(",", ":")), int(time.time())))

def hash_pw(password):
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 310000)
    return salt.hex() + ":" + digest.hex()

def verify_pw(password, encoded):
    try:
        salt, digest = encoded.split(":")
        check = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), 310000)
        return hmac.compare_digest(check, bytes.fromhex(digest))
    except Exception:
        return False

def money_minor(value):
    try:
        d = Decimal(str(value))
        cents = d * 100
        if not d.is_finite() or d <= 0 or cents != cents.to_integral_value() or cents > 100000000:
            raise ValueError()
        return int(cents)
    except (InvalidOperation, ValueError):
        raise HTTPException(422, "Amount must be positive USD with at most 2 decimals (max 1,000,000).")

def asset_minor(value):
    try:
        d = Decimal(str(value))
        units = d * Decimal(100000000)
        if not d.is_finite() or d <= 0 or units != units.to_integral_value():
            raise ValueError()
        return int(units)
    except (InvalidOperation, ValueError):
        raise HTTPException(422, "Invalid asset quantity.")

def ensure_account(c, uid, currency):
    c.execute("INSERT OR IGNORE INTO accounts(user_id,currency) VALUES(?,?)", (uid, currency))
    return c.execute("SELECT id FROM accounts WHERE user_id=? AND currency=?", (uid, currency)).fetchone()["id"]

def system_account(c, currency, purpose="house"):
    email = f"{purpose}-{currency.lower()}@internal.invalid"
    row = c.execute("SELECT id FROM users WHERE email=?", (email,)).fetchone()
    if row:
        uid = row["id"]
    else:
        uid = c.execute("INSERT INTO users(email,password_hash,role,created_at) VALUES(?,?,?,?)",
                        (email, hash_pw(secrets.token_urlsafe(48)), "system", int(time.time()))).lastrowid
    return ensure_account(c, uid, currency)

def post_journal(c, kind, reference, entries):
    # Each currency must independently balance to zero.
    totals = {}
    for account_id, currency, amount in entries:
        totals[currency] = totals.get(currency, 0) + amount
    if len(entries) < 2 or any(v != 0 for v in totals.values()) or any(v == 0 for _, _, v in entries):
        raise HTTPException(500, "Ledger invariant failed.")
    jid = secrets.token_urlsafe(18)
    c.execute("INSERT INTO journal(id,kind,reference,created_at) VALUES(?,?,?,?)",
              (jid, kind, reference, int(time.time())))
    for account_id, currency, amount in entries:
        c.execute("INSERT INTO postings(journal_id,account_id,amount_minor) VALUES(?,?,?)",
                  (jid, account_id, amount))
    return jid

def balance_minor(c, account_id):
    return c.execute("SELECT COALESCE(SUM(amount_minor),0) n FROM postings WHERE account_id=?",
                     (account_id,)).fetchone()["n"]

@app.on_event("startup")
def init():
    with db() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY,email TEXT UNIQUE NOT NULL,password_hash TEXT NOT NULL,role TEXT NOT NULL DEFAULT 'user',kyc_status TEXT NOT NULL DEFAULT 'not_submitted',created_at INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS sessions(id TEXT PRIMARY KEY,user_id INTEGER NOT NULL,expires_at INTEGER NOT NULL,revoked INTEGER NOT NULL DEFAULT 0,FOREIGN KEY(user_id) REFERENCES users(id));
        CREATE TABLE IF NOT EXISTS kyc(id INTEGER PRIMARY KEY,user_id INTEGER NOT NULL,legal_name TEXT NOT NULL,country TEXT NOT NULL,provider_reference TEXT,submitted_at INTEGER NOT NULL,FOREIGN KEY(user_id) REFERENCES users(id));
        CREATE TABLE IF NOT EXISTS accounts(id INTEGER PRIMARY KEY,user_id INTEGER NOT NULL,currency TEXT NOT NULL,UNIQUE(user_id,currency),FOREIGN KEY(user_id) REFERENCES users(id));
        CREATE TABLE IF NOT EXISTS journal(id TEXT PRIMARY KEY,kind TEXT NOT NULL,reference TEXT UNIQUE NOT NULL,created_at INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS postings(id INTEGER PRIMARY KEY,journal_id TEXT NOT NULL,account_id INTEGER NOT NULL,amount_minor INTEGER NOT NULL,FOREIGN KEY(journal_id) REFERENCES journal(id),FOREIGN KEY(account_id) REFERENCES accounts(id));
        CREATE TABLE IF NOT EXISTS orders(id TEXT PRIMARY KEY,user_id INTEGER NOT NULL,symbol TEXT NOT NULL,side TEXT NOT NULL,quantity TEXT NOT NULL,price TEXT NOT NULL,notional_minor INTEGER NOT NULL,fee_minor INTEGER NOT NULL,status TEXT NOT NULL,created_at INTEGER NOT NULL,FOREIGN KEY(user_id) REFERENCES users(id));
        CREATE TABLE IF NOT EXISTS limit_orders(id TEXT PRIMARY KEY,user_id INTEGER NOT NULL,symbol TEXT NOT NULL,side TEXT NOT NULL,quantity TEXT NOT NULL,limit_price TEXT NOT NULL,reserve_currency TEXT NOT NULL,reserve_minor INTEGER NOT NULL,status TEXT NOT NULL,created_at INTEGER NOT NULL,updated_at INTEGER NOT NULL,FOREIGN KEY(user_id) REFERENCES users(id));
        CREATE TABLE IF NOT EXISTS limit_trades(id TEXT PRIMARY KEY,symbol TEXT NOT NULL,buy_order_id TEXT NOT NULL,sell_order_id TEXT NOT NULL,buyer_user_id INTEGER NOT NULL,seller_user_id INTEGER NOT NULL,quantity TEXT NOT NULL,price TEXT NOT NULL,quote_minor INTEGER NOT NULL,buyer_fee_minor INTEGER NOT NULL,seller_fee_minor INTEGER NOT NULL,created_at INTEGER NOT NULL);
        CREATE INDEX IF NOT EXISTS limit_orders_user_status_idx ON limit_orders(user_id,status,created_at DESC);
        CREATE TABLE IF NOT EXISTS requests(id TEXT PRIMARY KEY,user_id INTEGER NOT NULL,kind TEXT NOT NULL,currency TEXT NOT NULL,amount_minor INTEGER NOT NULL,destination TEXT,status TEXT NOT NULL,created_at INTEGER NOT NULL,FOREIGN KEY(user_id) REFERENCES users(id));
        CREATE TABLE IF NOT EXISTS audit(id INTEGER PRIMARY KEY,actor_id INTEGER,event TEXT NOT NULL,detail TEXT NOT NULL,created_at INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS sandbox_operations(idempotency_key TEXT PRIMARY KEY,user_id INTEGER NOT NULL,operation TEXT NOT NULL,response TEXT NOT NULL,created_at INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS compliance_cases(id TEXT PRIMARY KEY,user_id INTEGER NOT NULL,reason TEXT NOT NULL,status TEXT NOT NULL DEFAULT 'open',created_at INTEGER NOT NULL,FOREIGN KEY(user_id) REFERENCES users(id));
        CREATE INDEX IF NOT EXISTS postings_account_idx ON postings(account_id);
        CREATE INDEX IF NOT EXISTS orders_user_time_idx ON orders(user_id,created_at DESC);
        CREATE TRIGGER IF NOT EXISTS journal_no_update BEFORE UPDATE ON journal BEGIN SELECT RAISE(ABORT,'immutable journal'); END;
        CREATE TRIGGER IF NOT EXISTS journal_no_delete BEFORE DELETE ON journal BEGIN SELECT RAISE(ABORT,'immutable journal'); END;
        CREATE TRIGGER IF NOT EXISTS postings_no_update BEFORE UPDATE ON postings BEGIN SELECT RAISE(ABORT,'immutable postings'); END;
        CREATE TRIGGER IF NOT EXISTS postings_no_delete BEFORE DELETE ON postings BEGIN SELECT RAISE(ABORT,'immutable postings'); END;
        """)
        # Safe additive migration for databases created by earlier prototype revisions.
        limit_cols = {r["name"] for r in c.execute("PRAGMA table_info(limit_orders)").fetchall()}
        if "remaining_quantity" not in limit_cols:
            c.execute("ALTER TABLE limit_orders ADD COLUMN remaining_quantity TEXT")
            c.execute("UPDATE limit_orders SET remaining_quantity=quantity WHERE remaining_quantity IS NULL")
        if "filled_quantity" not in limit_cols:
            c.execute("ALTER TABLE limit_orders ADD COLUMN filled_quantity TEXT NOT NULL DEFAULT '0'")
        c.execute("CREATE INDEX IF NOT EXISTS limit_book_match_idx ON limit_orders(symbol,side,status,created_at)")
        cols = {r["name"] for r in c.execute("PRAGMA table_info(orders)").fetchall()}
        if "notional_minor" not in cols:
            c.execute("ALTER TABLE orders ADD COLUMN notional_minor INTEGER NOT NULL DEFAULT 0")
        if "fee_minor" not in cols:
            c.execute("ALTER TABLE orders ADD COLUMN fee_minor INTEGER NOT NULL DEFAULT 0")

def require_user(credentials: HTTPAuthorizationCredentials | None = Depends(bearer)):
    if credentials is None:
        raise HTTPException(401, "Authentication required")
    try:
        sid = TOKEN.loads(credentials.credentials, max_age=3600)
    except (BadSignature, SignatureExpired):
        raise HTTPException(401, "Invalid or expired session")
    with db() as c:
        row = c.execute("""SELECT u.* FROM users u JOIN sessions s ON s.user_id=u.id
                           WHERE s.id=? AND s.revoked=0 AND s.expires_at>?""",
                        (sid, int(time.time()))).fetchone()
        if not row:
            raise HTTPException(401, "Session expired")
        return dict(row) | {"session_id": sid}

class Register(BaseModel):
    email: str = Field(min_length=5, max_length=254)
    password: str = Field(min_length=12, max_length=128)
class Login(BaseModel):
    email: str
    password: str
class PaperOrder(BaseModel):
    symbol: str = Field(pattern=r"^[A-Za-z0-9]{2,12}$")
    side: Literal["buy", "sell"]
    quote_amount: str = Field(min_length=1, max_length=32)
    reference_price: str = Field(min_length=1, max_length=32)
class LimitOrder(BaseModel):
    symbol: str = Field(pattern=r"^[A-Za-z0-9]{2,12}$")
    side: Literal["buy", "sell"]
    quantity: str = Field(min_length=1, max_length=32)
    limit_price: str = Field(min_length=1, max_length=32)
class PaperTransfer(BaseModel):
    amount: str
    currency: Literal["USD"] = "USD"
class CaseCreate(BaseModel):
    reason: str = Field(min_length=8, max_length=500)
class Kyc(BaseModel):
    legal_name: str = Field(min_length=2, max_length=150)
    country: str = Field(min_length=2, max_length=80)

@app.get("/health")
def health():
    return {"status": "ok", "mode": "ACCOUNT_LINK_ONLY", "legacy_paper_trading_enabled": PAPER_TRADING_ENABLED, "live_funds_enabled": False,
            "real_deposits_enabled": False, "real_withdrawals_enabled": False}

@app.post("/auth/register", status_code=201)
def register(body: Register):
    email = body.email.strip().lower()
    if "@" not in email or "." not in email.rsplit("@", 1)[-1]:
        raise HTTPException(422, "Invalid email")
    with db() as c:
        try:
            c.execute("BEGIN IMMEDIATE")
            uid = c.execute("INSERT INTO users(email,password_hash,created_at) VALUES(?,?,?)",
                            (email, hash_pw(body.password), int(time.time()))).lastrowid
            ensure_account(c, uid, "USD")
            audit(c, uid, "register", {"mode": "PAPER_ONLY"})
            c.execute("COMMIT")
        except sqlite3.IntegrityError:
            if c.in_transaction: c.execute("ROLLBACK")
            raise HTTPException(409, "Email already registered")
    return {"message": "Account created. Log in to connect your own exchange account.", "mode": "ACCOUNT_LINK_ONLY"}

@app.post("/auth/login")
def login(body: Login):
    with db() as c:
        u = c.execute("SELECT * FROM users WHERE email=?", (body.email.strip().lower(),)).fetchone()
        if not u or not verify_pw(body.password, u["password_hash"]):
            raise HTTPException(401, "Invalid credentials")
        sid = secrets.token_urlsafe(32)
        c.execute("INSERT INTO sessions(id,user_id,expires_at) VALUES(?,?,?)",
                  (sid, u["id"], int(time.time()) + 3600))
        audit(c, u["id"], "login", {})
    return {"access_token": TOKEN.dumps(sid), "token_type": "bearer", "expires_in": 3600}

@app.post("/auth/logout")
def logout(u=Depends(require_user)):
    with db() as c:
        c.execute("UPDATE sessions SET revoked=1 WHERE id=?", (u["session_id"],))
        audit(c, u["id"], "logout", {})
    return {"message": "Logged out"}

@app.get("/account/me")
def me(u=Depends(require_user)):
    return {"id": u["id"], "email": u["email"], "kyc_status": u["kyc_status"],
            "role": u["role"], "mode": "ACCOUNT_LINK_ONLY"}

@app.get("/trade/assets")
def trade_assets(u=Depends(require_user)):
    names = {"BTC": "Bitcoin", "ETH": "Ethereum", "SOL": "Solana", "BNB": "BNB", "XRP": "XRP"}
    return {
        "assets": [{"symbol": symbol, "name": names[symbol], "quote": "USD", "mode": "PAPER_ONLY"}
                   for symbol in sorted(SUPPORTED_PAPER_ASSETS)],
        "mode": "PAPER_ONLY",
    }

@app.get("/account/balances")
def balances(u=Depends(require_user)):
    with db() as c:
        rows = c.execute("""SELECT a.currency,COALESCE(SUM(p.amount_minor),0) balance
                            FROM accounts a LEFT JOIN postings p ON p.account_id=a.id
                            WHERE a.user_id=? GROUP BY a.id ORDER BY CASE WHEN a.currency='USD' THEN 0 ELSE 1 END,a.currency""",
                         (u["id"],)).fetchall()
    return {"balances": [{"currency": r["currency"],
            "amount": format(Decimal(r["balance"]) / (Decimal(100) if r["currency"] == "USD" else Decimal(100000000)), "f")}
            for r in rows], "mode": "PAPER_ONLY"}

@app.get("/trade/orders")
def list_orders(
    limit: int = 50,
    offset: int = 0,
    symbol: str | None = None,
    side: str | None = None,
    status: str | None = None,
    u=Depends(require_user),
):
    if not 1 <= limit <= 200:
        raise HTTPException(422, "limit must be between 1 and 200.")
    if offset < 0:
        raise HTTPException(422, "offset must be zero or greater.")
    clauses = ["user_id=?"]
    params: list[object] = [u["id"]]
    if symbol is not None:
        symbol = symbol.strip().upper()
        if symbol not in SUPPORTED_PAPER_ASSETS:
            raise HTTPException(422, "Unsupported paper asset filter.")
        clauses.append("symbol=?")
        params.append(symbol)
    if side is not None:
        side = side.strip().lower()
        if side not in {"buy", "sell"}:
            raise HTTPException(422, "side must be buy or sell.")
        clauses.append("side=?")
        params.append(side)
    if status is not None:
        status = status.strip().lower()
        if status != "filled":
            raise HTTPException(422, "Only filled orders are currently supported by the immediate-fill paper engine.")
        clauses.append("status=?")
        params.append(status)
    where = " AND ".join(clauses)
    with db() as c:
        total = c.execute(f"SELECT COUNT(*) n FROM orders WHERE {where}", params).fetchone()["n"]
        rows = c.execute(
            f"""SELECT id,symbol,side,quantity,price,notional_minor,fee_minor,status,created_at
                FROM orders WHERE {where}
                ORDER BY created_at DESC,id DESC LIMIT ? OFFSET ?""",
            [*params, limit, offset],
        ).fetchall()
    return {
        "orders": [{"id": r["id"], "symbol": r["symbol"], "side": r["side"],
            "quantity": r["quantity"], "price": r["price"],
            "notional": format(Decimal(r["notional_minor"])/100, ".2f"),
            "fee": format(Decimal(r["fee_minor"])/100, ".2f"),
            "status": r["status"], "created_at": r["created_at"]} for r in rows],
        "pagination": {"limit": limit, "offset": offset, "total": total,
            "has_more": offset + len(rows) < total},
        "filters": {"symbol": symbol, "side": side, "status": status},
        "mode": "PAPER_ONLY",
    }

@app.get("/trade/summary")
def trade_summary(u=Depends(require_user)):
    with db() as c:
        row = c.execute("""
            SELECT COUNT(*) AS order_count,
                   SUM(CASE WHEN status='filled' THEN 1 ELSE 0 END) AS filled_count,
                   SUM(CASE WHEN side='buy' THEN 1 ELSE 0 END) AS buy_count,
                   SUM(CASE WHEN side='sell' THEN 1 ELSE 0 END) AS sell_count,
                   COALESCE(SUM(CASE WHEN status='filled' THEN notional_minor ELSE 0 END),0) AS filled_notional_minor,
                   COALESCE(SUM(CASE WHEN status='filled' THEN fee_minor ELSE 0 END),0) AS fees_minor
            FROM orders WHERE user_id=?
        """, (u["id"],)).fetchone()
    return {
        "mode": "PAPER_ONLY",
        "summary": {
            "order_count": row["order_count"] or 0,
            "filled_count": row["filled_count"] or 0,
            "buy_count": row["buy_count"] or 0,
            "sell_count": row["sell_count"] or 0,
            "filled_notional": format(Decimal(row["filled_notional_minor"])/100, ".2f"),
            "fees": format(Decimal(row["fees_minor"])/100, ".2f"),
        },
        "notice": "Aggregates include recorded paper orders only; they are not live exchange volume or profit figures.",
    }


def _limit_order_required_reserve(row, remaining_units):
    if remaining_units <= 0:
        return 0
    if row["side"] == "sell":
        return remaining_units
    qty = Decimal(remaining_units) / Decimal(100000000)
    notional = int((qty * Decimal(row["limit_price"]) * 100).to_integral_value(rounding="ROUND_CEILING"))
    fee = max(1, int((Decimal(notional) * Decimal("0.001")).to_integral_value(rounding="ROUND_CEILING")))
    return notional + fee

def _release_limit_reserve(c, row, desired_reserve, now):
    excess = int(row["reserve_minor"]) - int(desired_reserve)
    if excess <= 0:
        return
    escrow = system_account(c, row["reserve_currency"], "paper-limit-reserve")
    user_account = ensure_account(c, row["user_id"], row["reserve_currency"])
    if balance_minor(c, escrow) < excess:
        raise HTTPException(500, "Limit order escrow integrity check failed.")
    post_journal(c, "paper_limit_release", row["id"] + ":match-release:" + str(now) + ":" + str(excess),
                 [(escrow, row["reserve_currency"], -excess), (user_account, row["reserve_currency"], excess)])
    c.execute("UPDATE limit_orders SET reserve_minor=reserve_minor-?,updated_at=? WHERE id=?", (excess, now, row["id"]))

def _match_limit_order(c, incoming_order_id, now):
    incoming = c.execute("SELECT * FROM limit_orders WHERE id=?", (incoming_order_id,)).fetchone()
    if not incoming:
        raise HTTPException(500, "Incoming limit order disappeared.")
    fills = []
    while True:
        incoming = c.execute("SELECT * FROM limit_orders WHERE id=?", (incoming_order_id,)).fetchone()
        incoming_units = int((Decimal(incoming["remaining_quantity"] or "0") * Decimal(100000000)).to_integral_value())
        if incoming["status"] not in ("open", "partially_filled") or incoming_units <= 0:
            break
        opposite = "sell" if incoming["side"] == "buy" else "buy"
        comparator = "<=" if incoming["side"] == "buy" else ">="
        sort = "ASC" if incoming["side"] == "buy" else "DESC"
        candidates = c.execute(
            "SELECT rowid AS insertion_seq,* FROM limit_orders WHERE symbol=? AND side=? AND status IN ('open','partially_filled') AND user_id<>? AND remaining_quantity IS NOT NULL",
            (incoming["symbol"], opposite, incoming["user_id"])
        ).fetchall()
        eligible = [r for r in candidates
                    if Decimal(r["remaining_quantity"] or "0") > 0
                    and ((Decimal(r["limit_price"]) <= Decimal(incoming["limit_price"])) if incoming["side"] == "buy"
                         else (Decimal(r["limit_price"]) >= Decimal(incoming["limit_price"])))]
        eligible.sort(key=lambda r: ((Decimal(r["limit_price"]) if incoming["side"] == "buy" else -Decimal(r["limit_price"])),
                                     r["created_at"], r["insertion_seq"]))
        maker = eligible[0] if eligible else None
        if not maker:
            break
        maker_units = int((Decimal(maker["remaining_quantity"] or "0") * Decimal(100000000)).to_integral_value())
        fill_units = min(incoming_units, maker_units)
        quantity = Decimal(fill_units) / Decimal(100000000)
        execution_price = Decimal(maker["limit_price"])
        quote_minor = int((quantity * execution_price * 100).to_integral_value(rounding="ROUND_DOWN"))
        if quote_minor <= 0:
            break
        buyer_order = incoming if incoming["side"] == "buy" else maker
        seller_order = maker if incoming["side"] == "buy" else incoming
        buyer_fee = int((Decimal(quote_minor) * Decimal("0.001")).to_integral_value(rounding="ROUND_DOWN"))
        seller_fee = int((Decimal(quote_minor) * Decimal("0.001")).to_integral_value(rounding="ROUND_DOWN"))
        buyer_debit = quote_minor + buyer_fee
        if int(buyer_order["reserve_minor"]) < buyer_debit:
            raise HTTPException(500, "Buyer limit-order reserve is insufficient for the matched fill.")
        if int(seller_order["reserve_minor"]) < fill_units:
            raise HTTPException(500, "Seller limit-order reserve is insufficient for the matched fill.")
        buyer_escrow = system_account(c, "USD", "paper-limit-reserve")
        seller_escrow = system_account(c, incoming["symbol"], "paper-limit-reserve")
        buyer_asset = ensure_account(c, buyer_order["user_id"], incoming["symbol"])
        seller_usd = ensure_account(c, seller_order["user_id"], "USD")
        fee_account = system_account(c, "USD", "paper-trading-fees")
        if balance_minor(c, buyer_escrow) < buyer_debit or balance_minor(c, seller_escrow) < fill_units:
            raise HTTPException(500, "Limit-order escrow balance integrity check failed.")
        usd_entries = [(buyer_escrow, "USD", -buyer_debit),
                       (seller_usd, "USD", quote_minor - seller_fee)]
        total_fees = buyer_fee + seller_fee
        if total_fees:
            usd_entries.append((fee_account, "USD", total_fees))
        post_journal(c, "paper_limit_trade_usd", "limit-trade:" + incoming_order_id + ":" + maker["id"] + ":" + str(now) + ":usd", usd_entries)
        post_journal(c, "paper_limit_trade_asset", "limit-trade:" + incoming_order_id + ":" + maker["id"] + ":" + str(now) + ":asset",
                     [(seller_escrow, incoming["symbol"], -fill_units), (buyer_asset, incoming["symbol"], fill_units)])
        trade_id = secrets.token_urlsafe(18)
        c.execute("INSERT INTO limit_trades(id,symbol,buy_order_id,sell_order_id,buyer_user_id,seller_user_id,quantity,price,quote_minor,buyer_fee_minor,seller_fee_minor,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                  (trade_id, incoming["symbol"], buyer_order["id"], seller_order["id"], buyer_order["user_id"], seller_order["user_id"], format(quantity,"f"), format(execution_price,"f"), quote_minor, buyer_fee, seller_fee, now))
        fills.append({"trade_id": trade_id, "quantity": format(quantity,"f"), "price": format(execution_price,"f"), "quote_amount": format(Decimal(quote_minor)/100,".2f"), "buyer_fee": format(Decimal(buyer_fee)/100,".2f"), "seller_fee": format(Decimal(seller_fee)/100,".2f"), "maker_order_id": maker["id"]})
        for order in (buyer_order, seller_order):
            current = c.execute("SELECT * FROM limit_orders WHERE id=?", (order["id"],)).fetchone()
            old_units = int((Decimal(current["remaining_quantity"]) * Decimal(100000000)).to_integral_value())
            new_units = old_units - fill_units
            new_filled = (Decimal(current["filled_quantity"] or "0") + quantity).quantize(Decimal("0.00000001"))
            new_remaining = (Decimal(new_units) / Decimal(100000000)).quantize(Decimal("0.00000001"))
            new_status = "filled" if new_units == 0 else "partially_filled"
            reserve_consumed = buyer_debit if current["id"] == buyer_order["id"] else fill_units
            if int(current["reserve_minor"]) < reserve_consumed:
                raise HTTPException(500, "Order reserve underflow during fill settlement.")
            c.execute("UPDATE limit_orders SET remaining_quantity=?,filled_quantity=?,reserve_minor=reserve_minor-?,status=?,updated_at=? WHERE id=?",
                      (format(new_remaining,"f"), format(new_filled,"f"), reserve_consumed, new_status, now, current["id"]))
            refreshed = c.execute("SELECT * FROM limit_orders WHERE id=?", (current["id"],)).fetchone()
            desired = _limit_order_required_reserve(refreshed, new_units)
            _release_limit_reserve(c, refreshed, desired, now)
        audit(c, incoming["user_id"], "paper_limit_order_matched", {"trade_id":trade_id,"incoming_order_id":incoming_order_id,"maker_order_id":maker["id"],"quantity":format(quantity,"f"),"price":format(execution_price,"f")})
    return fills

@app.post("/trade/limit-orders", status_code=201)
def create_limit_order(body: LimitOrder, idempotency_key: str = Header(min_length=8, max_length=100, alias="Idempotency-Key"), u=Depends(require_user)):
    symbol = body.symbol.upper()
    if symbol not in SUPPORTED_PAPER_ASSETS:
        raise HTTPException(422, "Unsupported paper asset.")
    try:
        qty = Decimal(body.quantity)
        price = Decimal(body.limit_price)
        qty_units = qty * Decimal(100000000)
        if not qty.is_finite() or not price.is_finite() or qty <= 0 or price <= 0 or qty_units != qty_units.to_integral_value() or qty > Decimal("1000000") or price > Decimal("1000000000") or price != price.quantize(Decimal("0.000000000001")):
            raise ValueError()
    except (InvalidOperation, ValueError):
        raise HTTPException(422, "Quantity must be positive with at most 8 decimals; limit price must be positive with at most 12 decimals.")
    asset_units = int(qty_units)
    notional_cents = int((qty * price * 100).to_integral_value(rounding="ROUND_CEILING"))
    fee_cents = max(1, int((Decimal(notional_cents) * Decimal("0.001")).to_integral_value(rounding="ROUND_CEILING")))
    if notional_cents <= 0 or notional_cents > 100000000:
        raise HTTPException(422, "Limit order notional must be between $0.01 and $1,000,000.")
    reserve_currency = "USD" if body.side == "buy" else symbol
    reserve_minor = notional_cents + fee_cents if body.side == "buy" else asset_units
    now = int(time.time())
    order_id = secrets.token_urlsafe(18)
    operation = f"limit:{u['id']}:{symbol}:{body.side}:{qty}:{price}"
    with db() as c:
        try:
            c.execute("BEGIN IMMEDIATE")
            prior = c.execute("SELECT user_id,operation,response FROM sandbox_operations WHERE idempotency_key=?", (idempotency_key,)).fetchone()
            if prior:
                if prior["user_id"] != u["id"] or prior["operation"] != operation:
                    raise HTTPException(409, "Idempotency key reused for a different limit order.")
                c.execute("COMMIT")
                return json.loads(prior["response"])
            user_account = ensure_account(c, u["id"], reserve_currency)
            escrow = system_account(c, reserve_currency, "paper-limit-reserve")
            if balance_minor(c, user_account) < reserve_minor:
                raise HTTPException(400, f"Insufficient available {reserve_currency} paper balance.")
            journal_id = post_journal(c, "paper_limit_reserve", order_id, [(user_account, reserve_currency, -reserve_minor), (escrow, reserve_currency, reserve_minor)])
            c.execute("INSERT INTO limit_orders(id,user_id,symbol,side,quantity,limit_price,reserve_currency,reserve_minor,status,created_at,updated_at,remaining_quantity,filled_quantity) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)", (order_id,u["id"],symbol,body.side,format(qty,"f"),format(price,"f"),reserve_currency,reserve_minor,"open",now,now,format(qty,"f"),"0"))
            fills = _match_limit_order(c, order_id, now)
            final_order = c.execute("SELECT * FROM limit_orders WHERE id=?", (order_id,)).fetchone()
            result = {"id":order_id,"symbol":symbol,"side":body.side,"quantity":format(qty,"f"),"filled_quantity":final_order["filled_quantity"],"remaining_quantity":final_order["remaining_quantity"],"limit_price":format(price,"f"),"reserve_currency":reserve_currency,"reserved_amount":format(Decimal(final_order["reserve_minor"])/(100 if reserve_currency=="USD" else 100000000), ".2f" if reserve_currency=="USD" else ".8f"),"status":final_order["status"],"fills":fills,"journal_id":journal_id,"mode":"PAPER_ONLY","execution_model":"PAPER_PRICE_TIME_PRIORITY_MATCHING"}
            c.execute("INSERT INTO sandbox_operations VALUES(?,?,?,?,?)",(idempotency_key,u["id"],operation,json.dumps(result),now))
            audit(c,u["id"],"paper_limit_order_opened",{"order_id":order_id,"symbol":symbol,"side":body.side,"reserve_currency":reserve_currency,"reserve_minor":reserve_minor})
            c.execute("COMMIT")
            return result
        except Exception:
            if c.in_transaction: c.execute("ROLLBACK")
            raise

@app.get("/trade/limit-order-book/{symbol}")
def limit_order_book(symbol: str, u=Depends(require_user)):
    symbol = symbol.upper()
    if symbol not in SUPPORTED_PAPER_ASSETS:
        raise HTTPException(422, "Unsupported paper asset.")
    with db() as c:
        rows = c.execute("SELECT side,limit_price,remaining_quantity AS quantity FROM limit_orders WHERE symbol=? AND status IN ('open','partially_filled') AND CAST(remaining_quantity AS REAL)>0 ORDER BY created_at ASC", (symbol,)).fetchall()
    levels = {"buy": {}, "sell": {}}
    for row in rows:
        price = Decimal(row["limit_price"])
        quantity = Decimal(row["quantity"])
        key = format(price, "f")
        if key not in levels[row["side"]]:
            levels[row["side"]][key] = {"price": price, "quantity": Decimal("0"), "order_count": 0}
        levels[row["side"]][key]["quantity"] += quantity
        levels[row["side"]][key]["order_count"] += 1
    bids = sorted(levels["buy"].values(), key=lambda x: x["price"], reverse=True)[:20]
    asks = sorted(levels["sell"].values(), key=lambda x: x["price"])[:20]
    def serialize(items):
        return [{"price": format(x["price"], "f"), "quantity": format(x["quantity"], "f"), "order_count": x["order_count"]} for x in items]
    return {"symbol": symbol, "bids": serialize(bids), "asks": serialize(asks), "mode": "PAPER_ONLY", "book_type": "ZENCRYPTO_USER_LIMIT_ORDERS", "execution_model": "AGGREGATED_RESTING_ORDERS_NO_MATCHING", "notice": "This is the ZenCrypto simulated user order book, not Binance market depth. User identities and order IDs are not exposed."}

@app.get("/trade/limit-orders")
def list_limit_orders(status: str | None = None, u=Depends(require_user)):
    if status is not None and status not in {"open","partially_filled","filled","cancelled"}:
        raise HTTPException(422, "status must be open, partially_filled, filled or cancelled.")
    with db() as c:
        if status:
            rows = c.execute("SELECT * FROM limit_orders WHERE user_id=? AND status=? ORDER BY created_at DESC",(u["id"],status)).fetchall()
        else:
            rows = c.execute("SELECT * FROM limit_orders WHERE user_id=? ORDER BY created_at DESC",(u["id"],)).fetchall()
    return {"orders":[{"id":r["id"],"symbol":r["symbol"],"side":r["side"],"quantity":r["quantity"],"filled_quantity":r["filled_quantity"],"remaining_quantity":r["remaining_quantity"],"limit_price":r["limit_price"],"reserve_currency":r["reserve_currency"],"reserved_amount":format(Decimal(r["reserve_minor"])/(100 if r["reserve_currency"]=="USD" else 100000000),".2f" if r["reserve_currency"]=="USD" else ".8f"),"status":r["status"],"created_at":r["created_at"],"updated_at":r["updated_at"]} for r in rows],"mode":"PAPER_ONLY","execution_model":"PAPER_PRICE_TIME_PRIORITY_MATCHING"}

@app.get("/trade/limit-trades")
def list_limit_trades(limit: int = 50, u=Depends(require_user)):
    if limit < 1 or limit > 200:
        raise HTTPException(422, "limit must be between 1 and 200.")
    with db() as c:
        rows = c.execute("SELECT * FROM limit_trades WHERE buyer_user_id=? OR seller_user_id=? ORDER BY created_at DESC,rowid DESC LIMIT ?", (u["id"],u["id"],limit)).fetchall()
    return {"trades":[{"id":r["id"],"symbol":r["symbol"],"side":"buy" if r["buyer_user_id"]==u["id"] else "sell","quantity":r["quantity"],"price":r["price"],"quote_amount":format(Decimal(r["quote_minor"])/100,".2f"),"fee":format(Decimal(r["buyer_fee_minor"] if r["buyer_user_id"]==u["id"] else r["seller_fee_minor"])/100,".2f"),"created_at":r["created_at"]} for r in rows],"mode":"PAPER_ONLY"}

@app.post("/trade/limit-orders/{order_id}/cancel")
def cancel_limit_order(order_id: str, u=Depends(require_user)):
    now = int(time.time())
    with db() as c:
        try:
            c.execute("BEGIN IMMEDIATE")
            row = c.execute("SELECT * FROM limit_orders WHERE id=? AND user_id=?",(order_id,u["id"])).fetchone()
            if not row:
                raise HTTPException(404,"Limit order not found.")
            if row["status"] == "cancelled":
                c.execute("COMMIT")
                return {"id":order_id,"status":"cancelled","already_cancelled":True,"mode":"PAPER_ONLY"}
            if row["status"] not in ("open", "partially_filled"):
                raise HTTPException(409,"Only open or partially filled limit orders can be cancelled.")
            user_account = ensure_account(c,u["id"],row["reserve_currency"])
            escrow = system_account(c,row["reserve_currency"],"paper-limit-reserve")
            if balance_minor(c,escrow) < row["reserve_minor"]:
                raise HTTPException(500,"Reserved paper balance integrity check failed.")
            journal_id = post_journal(c,"paper_limit_release",order_id+":cancel",[(escrow,row["reserve_currency"],-row["reserve_minor"]),(user_account,row["reserve_currency"],row["reserve_minor"])])
            c.execute("UPDATE limit_orders SET status='cancelled',reserve_minor=0,updated_at=? WHERE id=? AND status IN ('open','partially_filled')",(now,order_id))
            audit(c,u["id"],"paper_limit_order_cancelled",{"order_id":order_id,"released_currency":row["reserve_currency"],"released_minor":row["reserve_minor"]})
            c.execute("COMMIT")
            return {"id":order_id,"status":"cancelled","released_currency":row["reserve_currency"],"released_amount":format(Decimal(row["reserve_minor"])/(100 if row["reserve_currency"]=="USD" else 100000000),".2f" if row["reserve_currency"]=="USD" else ".8f"),"journal_id":journal_id,"mode":"PAPER_ONLY"}
        except Exception:
            if c.in_transaction: c.execute("ROLLBACK")
            raise

@app.post("/trade/preview")
def preview_order(body: PaperOrder, u=Depends(require_user)):
    symbol = body.symbol.upper()
    if symbol not in SUPPORTED_PAPER_ASSETS:
        raise HTTPException(422, "Unsupported paper asset. Supported assets: BTC, ETH, SOL, BNB, XRP.")
    try:
        price = Decimal(body.reference_price)
        quote = Decimal(body.quote_amount)
        if not price.is_finite() or not quote.is_finite() or price <= 0 or quote <= 0 or quote > Decimal("1000000") or price > Decimal("1000000000") or quote != quote.quantize(Decimal("0.01")) or price != price.quantize(Decimal("0.000000000001")):
            raise ValueError()
    except (InvalidOperation, ValueError):
        raise HTTPException(422, "Price must have at most 12 decimal places and USD order amount at most 2; both must be positive and within limits.")
    notional_minor = int((quote * 100).quantize(Decimal("1"), rounding=ROUND_DOWN))
    fee_minor = max(1, int((Decimal(notional_minor) * Decimal("0.001")).quantize(Decimal("1"), rounding=ROUND_DOWN)))
    qty = (Decimal(notional_minor) / 100 / price).quantize(Decimal("0.00000001"), rounding=ROUND_DOWN)
    asset_units = int(qty * Decimal(100000000))
    if notional_minor <= 0 or asset_units <= 0:
        raise HTTPException(422, "Order amount is too small at this price.")
    with db() as c:
        usd_row = c.execute("SELECT id FROM accounts WHERE user_id=? AND currency=?", (u["id"], "USD")).fetchone()
        asset_row = c.execute("SELECT id FROM accounts WHERE user_id=? AND currency=?", (u["id"], symbol)).fetchone()
        usd_available = balance_minor(c, usd_row["id"]) if usd_row else 0
        asset_available = balance_minor(c, asset_row["id"]) if asset_row else 0
    usd_required = notional_minor + fee_minor if body.side == "buy" else 0
    sufficient = usd_available >= usd_required if body.side == "buy" else asset_available >= asset_units
    return {
        "mode": "PAPER_ONLY",
        "symbol": symbol, "side": body.side,
        "quantity": format(qty, "f"),
        "reference_price": format(price, "f"),
        "notional": format(Decimal(notional_minor) / 100, ".2f"),
        "fee": format(Decimal(fee_minor) / 100, ".2f"),
        "estimated_usd_debit": format(Decimal(usd_required) / 100, ".2f") if body.side == "buy" else None,
        "estimated_usd_proceeds": format(Decimal(notional_minor - fee_minor) / 100, ".2f") if body.side == "sell" else None,
        "available_usd": format(Decimal(usd_available) / 100, ".2f"),
        "available_asset": format(Decimal(asset_available) / Decimal(100000000), ".8f"),
        "sufficient_balance": sufficient,
        "execution_model": "PREVIEW_ONLY_NO_EXECUTION",
        "price_source": "CLIENT_SUPPLIED_REFERENCE",
        "notice": "Read-only estimate. The supplied reference price is not an exchange quote; balances can change before order submission."
    }

@app.post("/trade/orders", status_code=201)
def create_order(body: PaperOrder, idempotency_key: str = Header(min_length=8, max_length=100, alias="Idempotency-Key"), u=Depends(require_user)):
    symbol = body.symbol.upper()
    if symbol not in SUPPORTED_PAPER_ASSETS:
        raise HTTPException(422, "Unsupported paper asset. Supported assets: BTC, ETH, SOL, BNB, XRP.")
    try:
        price = Decimal(body.reference_price)
        quote = Decimal(body.quote_amount)
        if not price.is_finite() or not quote.is_finite() or price <= 0 or quote <= 0 or quote > Decimal("1000000") or price > Decimal("1000000000") or quote != quote.quantize(Decimal("0.01")) or price != price.quantize(Decimal("0.000000000001")):
            raise ValueError()
    except (InvalidOperation, ValueError):
        raise HTTPException(422, "Price must have at most 12 decimal places and USD order amount at most 2; both must be positive and within limits.")
    notional_minor = int((quote * 100).quantize(Decimal("1"), rounding=ROUND_DOWN))
    if notional_minor:
        fee_minor = max(1, int((Decimal(notional_minor) * Decimal("0.001")).quantize(Decimal("1"), rounding=ROUND_DOWN)))
    else:
        raise HTTPException(422, "Order amount is too small.")
    qty = (Decimal(notional_minor) / 100 / price).quantize(Decimal("0.00000001"), rounding=ROUND_DOWN)
    asset_minor_units = int(qty * Decimal(100000000))
    if asset_minor_units <= 0:
        raise HTTPException(422, "Order amount is too small at this price.")
    # Fee is charged on top of buy notional and deducted from sell proceeds.
    now = int(time.time())
    trade_id = secrets.token_urlsafe(18)
    operation = f"trade:{u['id']}:{symbol}:{body.side}:{notional_minor}:{price}"
    with db() as c:
        try:
            c.execute("BEGIN IMMEDIATE")
            prior = c.execute("SELECT user_id,operation,response FROM sandbox_operations WHERE idempotency_key=?",
                              (idempotency_key,)).fetchone()
            if prior:
                if prior["user_id"] != u["id"] or prior["operation"] != operation:
                    raise HTTPException(409, "Idempotency key reused for a different order.")
                c.execute("COMMIT")
                return json.loads(prior["response"])
            usd_user = ensure_account(c, u["id"], "USD")
            asset_user = ensure_account(c, u["id"], symbol)
            usd_house = system_account(c, "USD", "paper-house")
            asset_house = system_account(c, symbol, "paper-house")
            fee_house = system_account(c, "USD", "paper-fees")
            if body.side == "buy":
                total_debit = notional_minor + fee_minor
                if balance_minor(c, usd_user) < total_debit:
                    raise HTTPException(400, "Insufficient simulated USD balance. Add paper test funds in Assets.")
                entries = [(usd_user, "USD", -total_debit), (usd_house, "USD", notional_minor),
                           (fee_house, "USD", fee_minor), (asset_user, symbol, asset_minor_units),
                           (asset_house, symbol, -asset_minor_units)]
            else:
                if balance_minor(c, asset_user) < asset_minor_units:
                    raise HTTPException(400, f"Insufficient {symbol} paper balance.")
                entries = [(asset_user, symbol, -asset_minor_units), (asset_house, symbol, asset_minor_units),
                           (usd_user, "USD", notional_minor-fee_minor), (usd_house, "USD", -notional_minor),
                           (fee_house, "USD", fee_minor)]
            journal_id = post_journal(c, "paper_trade", trade_id, entries)
            result = {"id": trade_id, "symbol": symbol, "side": body.side,
                      "quantity": format(qty, "f"), "price": format(price, "f"),
                      "notional": format(Decimal(notional_minor)/100, ".2f"),
                      "fee": format(Decimal(fee_minor)/100, ".2f"),
                      "status": "filled", "created_at": now, "journal_id": journal_id,
                      "execution_model": "IMMEDIATE_FILL_AT_CLIENT_REFERENCE_PRICE",
                      "price_source": "CLIENT_SUPPLIED_REFERENCE",
                      "mode": "PAPER_ONLY"}
            c.execute("""INSERT INTO orders(id,user_id,symbol,side,quantity,price,notional_minor,fee_minor,status,created_at)
                         VALUES(?,?,?,?,?,?,?,?,?,?)""",
                      (trade_id, u["id"], symbol, body.side, result["quantity"], result["price"],
                       notional_minor, fee_minor, "filled", now))
            c.execute("INSERT INTO sandbox_operations VALUES(?,?,?,?,?)",
                      (idempotency_key, u["id"], operation, json.dumps(result), now))
            audit(c, u["id"], "paper_order_filled", {"order_id": trade_id, "symbol": symbol, "side": body.side})
            c.execute("COMMIT")
            return result
        except Exception:
            if c.in_transaction: c.execute("ROLLBACK")
            raise

@app.post("/sandbox/faucet", status_code=201)
def faucet(body: PaperTransfer, idempotency_key: str = Header(min_length=8, max_length=100, alias="Idempotency-Key"), u=Depends(require_user)):
    amount = money_minor(body.amount)
    with db() as c:
        try:
            c.execute("BEGIN IMMEDIATE")
            prior = c.execute("SELECT user_id,operation,response FROM sandbox_operations WHERE idempotency_key=?",
                              (idempotency_key,)).fetchone()
            operation = f"faucet:{amount}:USD"
            if prior:
                if prior["user_id"] != u["id"] or prior["operation"] != operation:
                    raise HTTPException(409, "Idempotency key reused for a different operation.")
                c.execute("COMMIT")
                return json.loads(prior["response"])
            issued = c.execute("""SELECT COALESCE(SUM(CAST(json_extract(response,'$.credited_minor') AS INTEGER)),0)
                                  FROM sandbox_operations WHERE user_id=? AND operation LIKE 'faucet:%'""",
                               (u["id"],)).fetchone()[0]
            if issued + amount > 100000:
                raise HTTPException(422, "Lifetime paper test-fund limit is $1,000 per user.")
            user_usd = ensure_account(c, u["id"], "USD")
            house = system_account(c, "USD", "paper-faucet")
            jid = post_journal(c, "paper_faucet", "faucet:"+idempotency_key,
                               [(house, "USD", -amount), (user_usd, "USD", amount)])
            result = {"mode": "PAPER_ONLY", "credited_minor": amount, "currency": "USD", "journal_id": jid}
            c.execute("INSERT INTO sandbox_operations VALUES(?,?,?,?,?)",
                      (idempotency_key, u["id"], operation, json.dumps(result), int(time.time())))
            audit(c, u["id"], "paper_funds_credited", {"amount_minor": amount})
            c.execute("COMMIT")
            return result
        except Exception:
            if c.in_transaction: c.execute("ROLLBACK")
            raise

@app.get("/account/ledger")
def account_ledger(
    limit: int = 50,
    offset: int = 0,
    currency: str | None = None,
    u=Depends(require_user),
):
    if not 1 <= limit <= 200:
        raise HTTPException(422, "limit must be between 1 and 200.")
    if offset < 0:
        raise HTTPException(422, "offset must be zero or greater.")
    if currency is not None:
        currency = currency.strip().upper()
        if not re.fullmatch(r"[A-Z0-9]{2,12}", currency):
            raise HTTPException(422, "Invalid currency filter.")
    clauses = ["a.user_id=?"]
    params: list[object] = [u["id"]]
    if currency:
        clauses.append("a.currency=?")
        params.append(currency)
    where = " AND ".join(clauses)
    with db() as c:
        total = c.execute(
            f"""SELECT COUNT(*) n FROM postings p
                JOIN accounts a ON a.id=p.account_id
                JOIN journal j ON j.id=p.journal_id WHERE {where}""",
            params,
        ).fetchone()["n"]
        rows = c.execute(
            f"""SELECT j.id,j.kind,j.reference,j.created_at,a.currency,p.amount_minor
                FROM postings p JOIN accounts a ON a.id=p.account_id
                JOIN journal j ON j.id=p.journal_id WHERE {where}
                ORDER BY j.created_at DESC,j.id DESC,p.id DESC LIMIT ? OFFSET ?""",
            [*params, limit, offset],
        ).fetchall()
    return {
        "mode": "PAPER_ONLY",
        "entries": [dict(r) for r in rows],
        "pagination": {
            "limit": limit, "offset": offset, "total": total,
            "has_more": offset + len(rows) < total,
        },
        "filters": {"currency": currency},
    }

@app.get("/account/ledger/verify")
def verify_account_ledger(u=Depends(require_user)):
    with db() as c:
        checked = c.execute("""
            SELECT COUNT(DISTINCT j.id) n
            FROM journal j
            WHERE EXISTS (
                SELECT 1 FROM postings p JOIN accounts a ON a.id=p.account_id
                WHERE p.journal_id=j.id AND a.user_id=?
            )
        """, (u["id"],)).fetchone()["n"]
        issue_count = c.execute("""
            SELECT COUNT(*) n FROM (
                SELECT j.id, a.currency
                FROM journal j
                JOIN postings p ON p.journal_id=j.id
                JOIN accounts a ON a.id=p.account_id
                WHERE j.id IN (
                    SELECT DISTINCT p2.journal_id
                    FROM postings p2 JOIN accounts a2 ON a2.id=p2.account_id
                    WHERE a2.user_id=?
                )
                GROUP BY j.id, a.currency
                HAVING SUM(p.amount_minor) != 0
            )
        """, (u["id"],)).fetchone()["n"]
        issues = c.execute("""
            SELECT j.id AS journal_id, j.kind, a.currency, SUM(p.amount_minor) AS imbalance_minor
            FROM journal j
            JOIN postings p ON p.journal_id=j.id
            JOIN accounts a ON a.id=p.account_id
            WHERE j.id IN (
                SELECT DISTINCT p2.journal_id
                FROM postings p2 JOIN accounts a2 ON a2.id=p2.account_id
                WHERE a2.user_id=?
            )
            GROUP BY j.id, j.kind, a.currency
            HAVING SUM(p.amount_minor) != 0
            ORDER BY j.created_at DESC LIMIT 50
        """, (u["id"],)).fetchall()
    return {
        "mode": "PAPER_ONLY",
        "status": "balanced" if issue_count == 0 else "integrity_issue",
        "checked_journals": checked,
        "issue_count": issue_count,
        "issues": [dict(row) for row in issues],
        "notice": "Read-only integrity check of paper ledger postings; not an audit of real funds."
    }

# All real-money endpoints are hard-disabled. These routes never accept or move funds.
@app.post("/deposits/request", status_code=403)
def real_deposit_disabled():
    raise HTTPException(403, "Real deposits are disabled. Use the paper-funds faucet in the demo account.")
@app.post("/withdrawals/request", status_code=403)
def real_withdrawal_disabled():
    raise HTTPException(403, "Real withdrawals are disabled. No funds can be transferred.")

@app.post("/kyc/submit")
def submit_kyc(body: Kyc, u=Depends(require_user)):
    with db() as c:
        c.execute("INSERT INTO kyc(user_id,legal_name,country,submitted_at) VALUES(?,?,?,?)",
                  (u["id"], body.legal_name, body.country, int(time.time())))
        c.execute("UPDATE users SET kyc_status='pending_provider_verification' WHERE id=?", (u["id"],))
        audit(c, u["id"], "kyc_submitted", {"country": body.country})
    return {"status": "pending_provider_verification", "notice": "Demo only; no identity verification is performed."}

@app.get("/compliance/status")
def compliance_status(u=Depends(require_user)):
    return {"kyc_status": u["kyc_status"], "real_money_allowed": False, "withdrawals_allowed": False,
            "mode": "PAPER_ONLY"}

@app.post("/compliance/cases", status_code=201)
def open_case(body: CaseCreate, u=Depends(require_user)):
    case_id = secrets.token_urlsafe(18)
    with db() as c:
        c.execute("INSERT INTO compliance_cases(id,user_id,reason,created_at) VALUES(?,?,?,?)",
                  (case_id, u["id"], body.reason, int(time.time())))
        audit(c, u["id"], "compliance_case_opened", {"case_id": case_id})
    return {"case_id": case_id, "status": "open"}

@app.get("/admin/audit")
def admin_audit(u=Depends(require_user)):
    if u["role"] != "admin":
        raise HTTPException(403, "Admin role required")
    with db() as c:
        rows = c.execute("SELECT id,actor_id,event,detail,created_at FROM audit ORDER BY id DESC LIMIT 100").fetchall()
    return {"events": [dict(r) for r in rows]}

@app.get("/admin/ledger/integrity")
def ledger_integrity(u=Depends(require_user)):
    if u["role"] != "admin":
        raise HTTPException(403, "Admin role required")
    with db() as c:
        bad = c.execute("""SELECT j.id,a.currency FROM journal j JOIN postings p ON p.journal_id=j.id
                           JOIN accounts a ON a.id=p.account_id GROUP BY j.id,a.currency
                           HAVING COUNT(p.id)<2 OR SUM(p.amount_minor)<>0 LIMIT 100""").fetchall()
    return {"balanced": not bool(bad), "bad_journal_ids": [r["id"] for r in bad],
            "mode": "PAPER_ONLY", "notice": "Arithmetic integrity check only; not an independent audit."}
