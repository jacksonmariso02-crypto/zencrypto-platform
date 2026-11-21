"""ZenCrypto API. Simulated paper trading only; never transfers real assets."""
import os, sqlite3, secrets, hashlib, hmac, time, json, re
from decimal import Decimal, InvalidOperation, ROUND_DOWN
from contextlib import contextmanager
from typing import Literal
from fastapi import FastAPI, HTTPException, Header, Depends
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired

DB = os.getenv("DATABASE_PATH", "zencrypto.db")
SECRET = os.getenv("APP_SECRET")
if not SECRET:
    SECRET = secrets.token_urlsafe(48)
TOKEN = URLSafeTimedSerializer(SECRET, salt="zencrypto-session-v1")
bearer = HTTPBearer(auto_error=False)
app = FastAPI(title="ZenCrypto Paper Trading API", version="1.0.0")
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
    return {"status": "ok", "mode": "PAPER_ONLY", "live_funds_enabled": False,
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
    return {"message": "Account created. Log in to start paper trading.", "mode": "PAPER_ONLY"}

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
            "role": u["role"], "mode": "PAPER_ONLY"}

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
        if not re.fullmatch(r"[A-Z0-9]{2,20}", symbol):
            raise HTTPException(422, "Invalid symbol filter.")
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
        if status not in {"filled", "cancelled", "rejected", "open", "partially_filled"}:
            raise HTTPException(422, "Invalid order status filter.")
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

@app.post("/trade/orders", status_code=201)
def create_order(body: PaperOrder, idempotency_key: str = Header(min_length=8, max_length=100, alias="Idempotency-Key"), u=Depends(require_user)):
    symbol = body.symbol.upper()
    if symbol in {"USD", "USDT", "USDC"}:
        raise HTTPException(422, "Select a crypto asset symbol.")
    try:
        price = Decimal(body.reference_price)
        quote = Decimal(body.quote_amount)
        if not price.is_finite() or not quote.is_finite() or price <= 0 or quote <= 0 or quote > Decimal("1000000") or price > Decimal("1000000000"):
            raise ValueError()
    except (InvalidOperation, ValueError):
        raise HTTPException(422, "Price and order amount must be positive valid numbers.")
    notional_minor = int((quote * 100).quantize(Decimal("1"), rounding=ROUND_DOWN))
    if notional_minor:
        fee_minor = max(1, int((Decimal(notional_minor) * Decimal("0.001")).quantize(Decimal("1"), rounding=ROUND_DOWN)))
    else:
        raise HTTPException(422, "Order amount is too small.")
    qty = (Decimal(notional_minor - (fee_minor if body.side == "sell" else 0)) / 100 / price).quantize(Decimal("0.00000001"), rounding=ROUND_DOWN)
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
