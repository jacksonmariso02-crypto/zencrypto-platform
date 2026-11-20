"""ZenCrypto non-custodial API: server-side DEX quotes; users sign and submit transactions in their own wallets."""
import os, sqlite3, secrets, hashlib, hmac, time, json, re, math
from collections import defaultdict, deque
import httpx
from decimal import Decimal, InvalidOperation, ROUND_DOWN
from contextlib import contextmanager
from typing import Literal
from fastapi import FastAPI, HTTPException, Header, Depends
from fastapi.responses import JSONResponse
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
app = FastAPI(title="ZenCrypto Non-Custodial API", version="1.1.0")

# Best-effort per-process DEX request throttling. Use an edge/API gateway limit for multi-worker deployments.
DEX_RATE_LIMIT = int(os.getenv("DEX_RATE_LIMIT_PER_MINUTE", "30"))
_dex_requests = defaultdict(deque)

@app.middleware("http")
async def dex_api_safety_middleware(request, call_next):
    path = request.url.path
    if path.startswith("/dex/"):
        client_ip = request.client.host if request.client else "unknown"
        now = time.monotonic()
        window = _dex_requests[client_ip]
        while window and now - window[0] > 60:
            window.popleft()
        if len(window) >= max(1, DEX_RATE_LIMIT):
            return JSONResponse(status_code=429, content={"detail": "Too many quote requests. Please wait and retry."},
                                headers={"Retry-After": "60"})
        window.append(now)
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Content-Security-Policy"] = "frame-ancestors 'none'; base-uri 'none'"
    response.headers["X-Permitted-Cross-Domain-Policies"] = "none"
    response.headers["X-XSS-Protection"] = "0"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Cache-Control"] = "no-store"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    return response

PAPER_ROUTE_PREFIXES = ("/auth/", "/account/", "/trade/", "/sandbox/", "/admin/", "/deposits/", "/withdrawals/", "/kyc/", "/compliance/cases")

@app.middleware("http")
async def disable_legacy_paper_api_by_default(request, call_next):
    if request.url.path.startswith(PAPER_ROUTE_PREFIXES) and os.getenv("ENABLE_PAPER_TRADING", "false").lower() != "true":
        return JSONResponse(status_code=404, content={"detail": "Legacy paper-trading API is disabled. ZenCrypto wallet and DEX routes are separate."})
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
class SandboxDeposit(BaseModel):
    amount: str
    currency: Literal["USD"] = "USD"

class SandboxWithdrawal(BaseModel):
    amount: str
    currency: Literal["USD"] = "USD"
    destination_label: str = Field(min_length=3, max_length=120)

class WithdrawalResolution(BaseModel):
    decision: Literal["approve", "reject"]
    note: str = Field(min_length=3, max_length=300)

class CaseCreate(BaseModel):
    reason: str = Field(min_length=8, max_length=500)
class Kyc(BaseModel):
    legal_name: str = Field(min_length=2, max_length=150)
    country: str = Field(min_length=2, max_length=80)



@app.get("/market/candles")
async def market_candles(symbol: str = "BTCUSDT", interval: str = "1h", limit: int = 300, end_time: int | None = None):
    """Real Binance Spot OHLCV candles. This endpoint is market data, never an execution quote."""
    allowed_symbols = {"BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT"}
    allowed_intervals = {"1m", "3m", "5m", "15m", "30m", "1h", "2h", "4h", "6h", "8h", "12h", "1d", "3d", "1w", "1M"}
    symbol = symbol.upper()
    if symbol not in allowed_symbols:
        raise HTTPException(422, "Unsupported chart symbol.")
    if interval not in allowed_intervals:
        raise HTTPException(422, "Unsupported Binance Spot candle interval.")
    if limit < 50 or limit > 1000:
        raise HTTPException(422, "Candle limit must be between 50 and 1000.")
    if end_time is not None and (end_time < 1 or end_time > int(time.time() * 1000)):
        raise HTTPException(422, "end_time must be a valid past Unix timestamp in milliseconds.")
    query_params = {"symbol": symbol, "interval": interval, "limit": limit}
    if end_time is not None:
        query_params["endTime"] = end_time
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.get(
                "https://api.binance.com/api/v3/klines",
                params=query_params,
                headers={"Accept": "application/json"},
            )
        if response.status_code >= 400:
            raise HTTPException(502, "Binance market data is temporarily unavailable.")
        raw = response.json()
        if not isinstance(raw, list):
            raise HTTPException(502, "Malformed Binance candle response.")

        server_time_ms = int(time.time() * 1000)
        by_open_time = {}
        for row in raw:
            if not isinstance(row, list) or len(row) < 6:
                continue
            try:
                open_time_ms = int(row[0])
                close_time_ms = int(row[6]) if len(row) > 6 else open_time_ms
                candle = {
                    "time": open_time_ms // 1000,
                    "open": float(row[1]),
                    "high": float(row[2]),
                    "low": float(row[3]),
                    "close": float(row[4]),
                    "volume": float(row[5]),
                    "close_time": close_time_ms // 1000,
                    "quote_volume": float(row[7]) if len(row) > 7 else 0.0,
                    "trades": int(row[8]) if len(row) > 8 else 0,
                    "is_closed": close_time_ms < server_time_ms,
                }
            except (TypeError, ValueError, OverflowError):
                continue
            prices = (candle["open"], candle["high"], candle["low"], candle["close"])
            volumes = (candle["volume"], candle["quote_volume"])
            if (
                open_time_ms <= 0
                or close_time_ms <= open_time_ms
                or not all(math.isfinite(value) and value > 0 for value in prices)
                or not all(math.isfinite(value) and value >= 0 for value in volumes)
                or candle["trades"] < 0
                or candle["high"] < max(candle["open"], candle["close"])
                or candle["low"] > min(candle["open"], candle["close"])
                or candle["low"] <= 0
            ):
                continue
            by_open_time[candle["time"]] = candle

        candles = [by_open_time[key] for key in sorted(by_open_time)]
        if len(candles) < 2:
            raise HTTPException(502, "Binance returned fewer than two valid candles.")
        return {
            "symbol": symbol,
            "interval": interval,
            "market": "SPOT",
            "provider": "Binance Spot REST API",
            "source": "Binance public market data",
            "server_time": server_time_ms // 1000,
            "candle_count": len(candles),
            "candles": candles,
            "notice": "Real exchange OHLCV market data. Indicative only; not an executable DEX quote or guaranteed fill price.",
        }
    except httpx.TimeoutException:
        raise HTTPException(504, "Binance market data request timed out.")
    except httpx.HTTPError:
        raise HTTPException(502, "Binance market data provider is temporarily unavailable.")

@app.get("/chain/history")
async def chain_history(chainId: int, address: str, limit: int = 25):
    """Indexed public wallet activity from Routescan; never signs or broadcasts transactions."""
    if chainId not in (1, 8453):
        raise HTTPException(422, "Indexed history is currently available for Ethereum and Base mainnet only.")
    if not re.fullmatch(r"0x[a-fA-F0-9]{40}", address):
        raise HTTPException(422, "Invalid wallet address.")
    if limit < 1 or limit > 50:
        raise HTTPException(422, "Limit must be between 1 and 50.")
    api_key = os.getenv("ROUTESCAN_API_KEY", "").strip()
    base = f"https://api.routescan.io/v2/network/mainnet/evm/{chainId}/address/{address}"
    headers = {"Accept": "application/json"}
    if api_key:
        headers["apikey"] = api_key
    try:
        async with httpx.AsyncClient(timeout=18.0) as client:
            tx_response, token_response = await client.get(
                base + "/transactions",
                params={"categories": "evm_tx", "sort": "desc", "limit": str(limit)},
                headers=headers,
            ), await client.get(
                base + "/erc20-transfers",
                params={"sort": "desc", "limit": str(limit)},
                headers=headers,
            )
        if tx_response.status_code >= 400 or token_response.status_code >= 400:
            raise HTTPException(502, "Indexed history provider is temporarily unavailable.")
        tx_data, token_data = tx_response.json(), token_response.json()
        if not isinstance(tx_data, dict) or not isinstance(tx_data.get("items", []), list):
            raise HTTPException(502, "Malformed transaction history response.")
        if not isinstance(token_data, dict) or not isinstance(token_data.get("items", []), list):
            raise HTTPException(502, "Malformed token transfer response.")
        token_allowlist = {
            1: {
                "0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2": {"symbol": "WETH", "decimals": 18},
                "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48": {"symbol": "USDC", "decimals": 6},
            },
            8453: {
                "0x4200000000000000000000000000000000000006": {"symbol": "WETH", "decimals": 18},
                "0x833589fcd6edb6e08f4c7c32d4f71b54bdA02913".lower(): {"symbol": "USDC", "decimals": 6},
            },
        }
        items = []
        for item in tx_data.get("items", []):
            if not isinstance(item, dict):
                continue
            tx_hash = item.get("hash") or item.get("txHash")
            if isinstance(tx_hash, str) and re.fullmatch(r"0x[a-fA-F0-9]{64}", tx_hash):
                items.append({"kind": "transaction", "hash": tx_hash, "timestamp": item.get("timestamp") or item.get("createdAt"), "from": item.get("from"), "to": item.get("to"), "value": item.get("value"), "status": item.get("status"), "blockNumber": item.get("blockNumber")})
        for item in token_data.get("items", []):
            if not isinstance(item, dict):
                continue
            tx_hash = item.get("txHash")
            if isinstance(tx_hash, str) and re.fullmatch(r"0x[a-fA-F0-9]{64}", tx_hash):
                contract = item.get("tokenAddress") or item.get("contractAddress")
                contract_key = contract.lower() if isinstance(contract, str) and re.fullmatch(r"0x[a-fA-F0-9]{40}", contract) else None
                trusted = token_allowlist[chainId].get(contract_key) if contract_key else None
                items.append({
                    "kind": "token_transfer", "hash": tx_hash,
                    "timestamp": item.get("timestamp") or item.get("createdAt"),
                    "from": item.get("from"), "to": item.get("to"),
                    "value": item.get("amount"),
                    "token": trusted["symbol"] if trusted else (item.get("tokenSymbol") or item.get("tokenName") or "Unknown token"),
                    "decimals": trusted["decimals"] if trusted else item.get("tokenDecimals"),
                    "tokenAddress": contract if contract_key else None,
                    "contractVerified": bool(trusted),
                    "status": "indexed", "blockNumber": item.get("blockNumber")
                })
        items.sort(key=lambda x: str(x.get("timestamp") or ""), reverse=True)
        return {"chainId": chainId, "address": address.lower(), "items": items[:limit], "source": "Routescan", "notice": "Indexed results can lag the chain and may omit unsupported transaction types. Verify final status in the block explorer."}
    except httpx.TimeoutException:
        raise HTTPException(504, "Indexed history request timed out.")
    except httpx.HTTPError:
        raise HTTPException(502, "Indexed history provider is temporarily unavailable.")

@app.get("/health")
def health():
    # Liveness only: this endpoint does not claim trading or production readiness.
    return {"status": "ok", "service": "zencrypto-noncustodial-api", "version": app.version}

@app.get("/ready")
def readiness():
    origins = [x.strip() for x in os.getenv("CORS_ORIGINS", "").split(",") if x.strip()]
    paper_enabled = os.getenv("ENABLE_PAPER_TRADING", "false").lower() == "true"
    checks = {
        "dex_provider_key_configured": bool(os.getenv("ZEROX_API_KEY", "").strip()),
        "explicit_cors_origins_configured": bool(origins),
        "paper_trading_disabled": not paper_enabled,
        "persistent_app_secret_configured": bool(os.getenv("APP_SECRET", "").strip()),
    }
    ready = all(checks.values())
    return JSONResponse(
        status_code=200 if ready else 503,
        content={
            "ready": ready,
            "service": "zencrypto-noncustodial-api",
            "checks": checks,
            "notice": "Operational configuration only; this is not a legal, licensing, security-audit, or mainnet-trading approval."
        }
    )

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
def list_orders(u=Depends(require_user)):
    with db() as c:
        rows = c.execute("""SELECT id,symbol,side,quantity,price,notional_minor,fee_minor,status,created_at
                            FROM orders WHERE user_id=? ORDER BY created_at DESC LIMIT 100""",
                         (u["id"],)).fetchall()
    return {"orders": [{"id": r["id"], "symbol": r["symbol"], "side": r["side"],
            "quantity": r["quantity"], "price": r["price"],
            "notional": format(Decimal(r["notional_minor"])/100, ".2f"),
            "fee": format(Decimal(r["fee_minor"])/100, ".2f"),
            "status": r["status"], "created_at": r["created_at"]} for r in rows],
            "mode": "PAPER_ONLY"}

@app.post("/trade/orders", status_code=201)
def create_order(body: PaperOrder, idempotency_key: str = Header(min_length=8, max_length=100, alias="Idempotency-Key"), u=Depends(require_user)):
    symbol = body.symbol.upper()
    allowed_symbols = {"BTC", "ETH", "SOL", "BNB", "XRP"}
    if symbol not in allowed_symbols:
        raise HTTPException(422, "Unsupported paper-trading asset. Supported assets: BTC, ETH, SOL, BNB, XRP.")
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


# Test-workflow endpoints. These only move virtual USD inside the paper ledger.
# They do not connect to a blockchain, bank, external wallet or payment rail.
@app.post("/sandbox/deposits", status_code=201)
def sandbox_deposit(body: SandboxDeposit, idempotency_key: str = Header(min_length=8, max_length=100, alias="Idempotency-Key"), u=Depends(require_user)):
    amount = money_minor(body.amount)
    with db() as c:
        try:
            c.execute("BEGIN IMMEDIATE")
            prior = c.execute("SELECT user_id,operation,response FROM sandbox_operations WHERE idempotency_key=?", (idempotency_key,)).fetchone()
            operation = f"sandbox_deposit:{amount}:USD"
            if prior:
                if prior["user_id"] != u["id"] or prior["operation"] != operation:
                    raise HTTPException(409, "Idempotency key reused for a different deposit.")
                c.execute("COMMIT")
                return json.loads(prior["response"])
            issued = c.execute("""SELECT COALESCE(SUM(CAST(json_extract(response,'$.credited_minor') AS INTEGER)),0)
                                  FROM sandbox_operations WHERE user_id=? AND operation LIKE 'sandbox_deposit:%'""", (u["id"],)).fetchone()[0]
            if issued + amount > 100000:
                raise HTTPException(422, "Sandbox deposit limit is $1,000 per user.")
            user_usd = ensure_account(c, u["id"], "USD")
            faucet = system_account(c, "USD", "paper-faucet")
            request_id = secrets.token_urlsafe(18)
            journal_id = post_journal(c, "sandbox_deposit", request_id, [(faucet, "USD", -amount), (user_usd, "USD", amount)])
            now = int(time.time())
            result = {"id": request_id, "currency": "USD", "amount": format(Decimal(amount)/100, ".2f"),
                      "status": "sandbox_credited", "mode": "PAPER_ONLY", "journal_id": journal_id,
                      "notice": "Virtual test funds only. No blockchain deposit or real payment occurred."}
            c.execute("INSERT INTO requests(id,user_id,kind,currency,amount_minor,destination,status,created_at) VALUES(?,?,?,?,?,?,?,?)",
                      (request_id, u["id"], "sandbox_deposit", "USD", amount, None, "sandbox_credited", now))
            c.execute("INSERT INTO sandbox_operations VALUES(?,?,?,?,?)",
                      (idempotency_key, u["id"], operation, json.dumps(result), now))
            audit(c, u["id"], "sandbox_deposit_credited", {"request_id": request_id, "amount_minor": amount})
            c.execute("COMMIT")
            return result
        except Exception:
            if c.in_transaction: c.execute("ROLLBACK")
            raise

@app.post("/sandbox/withdrawal-requests", status_code=201)
def sandbox_withdrawal(body: SandboxWithdrawal, idempotency_key: str = Header(min_length=8, max_length=100, alias="Idempotency-Key"), u=Depends(require_user)):
    amount = money_minor(body.amount)
    destination = body.destination_label.strip()
    with db() as c:
        try:
            c.execute("BEGIN IMMEDIATE")
            prior = c.execute("SELECT user_id,operation,response FROM sandbox_operations WHERE idempotency_key=?", (idempotency_key,)).fetchone()
            operation = f"sandbox_withdrawal:{amount}:USD:{destination}"
            if prior:
                if prior["user_id"] != u["id"] or prior["operation"] != operation:
                    raise HTTPException(409, "Idempotency key reused for a different withdrawal.")
                c.execute("COMMIT")
                return json.loads(prior["response"])
            user_usd = ensure_account(c, u["id"], "USD")
            if balance_minor(c, user_usd) < amount:
                raise HTTPException(400, "Insufficient simulated USD balance.")
            hold = system_account(c, "USD", "paper-withdrawal-hold")
            request_id = secrets.token_urlsafe(18)
            journal_id = post_journal(c, "sandbox_withdrawal_hold", request_id,
                                      [(user_usd, "USD", -amount), (hold, "USD", amount)])
            now = int(time.time())
            result = {"id": request_id, "currency": "USD", "amount": format(Decimal(amount)/100, ".2f"),
                      "destination_label": destination, "status": "pending_sandbox_review", "mode": "PAPER_ONLY",
                      "journal_id": journal_id,
                      "notice": "Virtual balance reserved for workflow testing only. No external transfer will occur."}
            c.execute("INSERT INTO requests(id,user_id,kind,currency,amount_minor,destination,status,created_at) VALUES(?,?,?,?,?,?,?,?)",
                      (request_id, u["id"], "sandbox_withdrawal", "USD", amount, destination, "pending_sandbox_review", now))
            c.execute("INSERT INTO sandbox_operations VALUES(?,?,?,?,?)",
                      (idempotency_key, u["id"], operation, json.dumps(result), now))
            audit(c, u["id"], "sandbox_withdrawal_requested", {"request_id": request_id, "amount_minor": amount})
            c.execute("COMMIT")
            return result
        except Exception:
            if c.in_transaction: c.execute("ROLLBACK")
            raise

@app.get("/account/requests")
def account_requests(u=Depends(require_user)):
    with db() as c:
        rows = c.execute("""SELECT id,kind,currency,amount_minor,destination,status,created_at
                            FROM requests WHERE user_id=? ORDER BY created_at DESC LIMIT 100""", (u["id"],)).fetchall()
    return {"mode": "PAPER_ONLY", "requests": [
        {"id": r["id"], "kind": r["kind"], "currency": r["currency"],
         "amount": format(Decimal(r["amount_minor"])/100, ".2f"),
         "destination_label": r["destination"], "status": r["status"], "created_at": r["created_at"]}
        for r in rows]}

@app.get("/admin/requests")
def admin_requests(u=Depends(require_user)):
    if u["role"] != "admin":
        raise HTTPException(403, "Admin role required")
    with db() as c:
        rows = c.execute("""SELECT r.id,r.user_id,u.email,r.kind,r.currency,r.amount_minor,r.destination,r.status,r.created_at
                            FROM requests r JOIN users u ON u.id=r.user_id ORDER BY r.created_at DESC LIMIT 200""").fetchall()
    return {"mode": "PAPER_ONLY", "requests": [
        {"id": r["id"], "user_id": r["user_id"], "email": r["email"], "kind": r["kind"],
         "currency": r["currency"], "amount": format(Decimal(r["amount_minor"])/100, ".2f"),
         "destination_label": r["destination"], "status": r["status"], "created_at": r["created_at"]}
        for r in rows]}

@app.post("/admin/requests/{request_id}/resolve")
def resolve_sandbox_request(request_id: str, body: WithdrawalResolution, u=Depends(require_user)):
    if u["role"] != "admin":
        raise HTTPException(403, "Admin role required")
    with db() as c:
        try:
            c.execute("BEGIN IMMEDIATE")
            req = c.execute("SELECT * FROM requests WHERE id=? AND kind='sandbox_withdrawal'", (request_id,)).fetchone()
            if not req:
                raise HTTPException(404, "Sandbox withdrawal request not found.")
            if req["status"] != "pending_sandbox_review":
                raise HTTPException(409, "Request has already been resolved.")
            user_usd = ensure_account(c, req["user_id"], "USD")
            hold = system_account(c, "USD", "paper-withdrawal-hold")
            amount = req["amount_minor"]
            if body.decision == "approve":
                sink = system_account(c, "USD", "paper-withdrawal-simulated-settlement")
                journal_id = post_journal(c, "sandbox_withdrawal_approved", request_id + ":approved",
                                          [(hold, "USD", -amount), (sink, "USD", amount)])
                new_status = "sandbox_approved"
            else:
                journal_id = post_journal(c, "sandbox_withdrawal_rejected", request_id + ":rejected",
                                          [(hold, "USD", -amount), (user_usd, "USD", amount)])
                new_status = "sandbox_rejected"
            c.execute("UPDATE requests SET status=? WHERE id=?", (new_status, request_id))
            audit(c, u["id"], "sandbox_withdrawal_resolved",
                  {"request_id": request_id, "decision": body.decision, "note": body.note, "journal_id": journal_id})
            c.execute("COMMIT")
            return {"id": request_id, "status": new_status, "mode": "PAPER_ONLY",
                    "notice": "Sandbox ledger state updated. No external funds were transferred."}
        except Exception:
            if c.in_transaction: c.execute("ROLLBACK")
            raise

@app.get("/account/ledger")
def account_ledger(u=Depends(require_user)):
    with db() as c:
        rows = c.execute("""SELECT j.id,j.kind,j.reference,j.created_at,a.currency,p.amount_minor
                            FROM postings p JOIN accounts a ON a.id=p.account_id
                            JOIN journal j ON j.id=p.journal_id WHERE a.user_id=?
                            ORDER BY j.created_at DESC LIMIT 200""", (u["id"],)).fetchall()
    return {"mode": "PAPER_ONLY", "entries": [dict(r) for r in rows]}

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


# Non-custodial DEX quote proxy. The 0x API key is server-side only.
DEX_TOKENS = {
    1: {
        "0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2": "WETH",
        "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48": "USDC",
    },
    8453: {
        "0x4200000000000000000000000000000000000006": "WETH",
        "0x833589fcd6edb6e08f4c7c32d4f71b54bda02913": "USDC",
    },
}
ZEROX_ALLOWANCE_HOLDER = "0x0000000000001ff3684f28c67538d4d072c22734"

def validate_dex_request(chain_id: int, sell_token: str, buy_token: str, sell_amount: str, taker: str):
    if chain_id not in DEX_TOKENS:
        raise HTTPException(422, "DEX quotes are enabled only for Ethereum Mainnet and Base Mainnet.")
    sell = sell_token.lower()
    buy = buy_token.lower()
    if sell not in DEX_TOKENS[chain_id] or buy not in DEX_TOKENS[chain_id] or sell == buy:
        raise HTTPException(422, "Unsupported token pair. Only the curated WETH/USDC pair is enabled.")
    if not sell_amount.isascii() or not sell_amount.isdigit() or int(sell_amount) <= 0 or len(sell_amount) > 78:
        raise HTTPException(422, "sellAmount must be a positive integer in token base units.")
    if not (len(taker) == 42 and taker.startswith("0x") and all(ch in "0123456789abcdefABCDEF" for ch in taker[2:])):
        raise HTTPException(422, "Invalid wallet address.")
    return sell, buy

async def call_zerox(endpoint: str, params: dict):
    api_key = os.getenv("ZEROX_API_KEY", "").strip()
    if not api_key:
        raise HTTPException(503, "DEX quotes are not configured. Add ZEROX_API_KEY to the backend environment.")
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.get(
                "https://api.0x.org" + endpoint,
                params=params,
                headers={"0x-api-key": api_key, "0x-version": "v2", "Accept": "application/json"},
            )
        if response.status_code >= 400:
            detail = "Quote provider rejected the request."
            try:
                detail = response.json().get("reason") or response.json().get("message") or detail
            except Exception:
                pass
            raise HTTPException(502, detail[:300])
        return response.json()
    except httpx.TimeoutException:
        raise HTTPException(504, "Quote provider timed out. Request a fresh quote.")
    except httpx.HTTPError:
        raise HTTPException(502, "Quote provider is temporarily unavailable.")

def validate_dex_quote_response(data: dict, *, chain_id: int, sell_token: str, buy_token: str, sell_amount: str, taker: str):
    """Reject mismatched provider payloads before returning wallet-executable calldata."""
    if not isinstance(data, dict):
        raise HTTPException(502, "Invalid quote provider response.")
    if data.get("liquidityAvailable") is not True:
        raise HTTPException(422, "No executable liquidity is available for this pair and amount.")
    for field, expected in (("sellToken", sell_token), ("buyToken", buy_token)):
        actual = data.get(field)
        if not isinstance(actual, str) or actual.lower() != expected:
            raise HTTPException(502, "Quote provider returned a mismatched token.")
    if str(data.get("sellAmount", "")) != sell_amount:
        raise HTTPException(502, "Quote provider returned a mismatched sell amount.")
    buy_amount = str(data.get("buyAmount", ""))
    if not buy_amount.isascii() or not buy_amount.isdigit() or int(buy_amount) <= 0:
        raise HTTPException(502, "Quote provider returned an invalid buy amount.")
    min_buy_amount = data.get("minBuyAmount")
    if min_buy_amount is None or not str(min_buy_amount).isascii() or not str(min_buy_amount).isdigit() or int(min_buy_amount) <= 0 or int(min_buy_amount) > int(buy_amount):
        raise HTTPException(502, "Quote provider returned an invalid minimum output.")
    issues = data.get("issues")
    if not isinstance(issues, dict):
        raise HTTPException(502, "Quote provider did not provide execution safety diagnostics.")
    if issues.get("simulationIncomplete") is True:
        raise HTTPException(422, "Quote simulation is incomplete; execution is blocked.")
    if issues.get("invalidSourcesPassed"):
        raise HTTPException(422, "Quote provider reported invalid liquidity sources.")
    balance = issues.get("balance")
    if balance:
        try:
            if int(balance["actual"]) < int(balance["expected"]):
                raise HTTPException(422, "Insufficient token balance for the swap.")
        except (ValueError, TypeError, KeyError):
            raise HTTPException(502, "Malformed balance diagnostics from quote provider.")
    tx = data.get("transaction")
    if not isinstance(tx, dict):
        raise HTTPException(502, "Quote provider did not return a transaction.")
    if not isinstance(tx.get("from"), str) or tx["from"].lower() != taker.lower():
        raise HTTPException(502, "Quote taker does not match the connected wallet.")
    target = tx.get("to")
    if not isinstance(target, str) or len(target) != 42 or not target.startswith("0x") or any(c not in "0123456789abcdefABCDEF" for c in target[2:]):
        raise HTTPException(502, "Quote contains an invalid transaction target.")
    calldata = tx.get("data")
    if not isinstance(calldata, str) or not calldata.startswith("0x") or len(calldata) < 10 or len(calldata) % 2 or any(c not in "0123456789abcdefABCDEF" for c in calldata[2:]):
        raise HTTPException(502, "Quote contains invalid transaction calldata.")
    if str(tx.get("value", "0")) not in ("0", "0x0", "0x00"):
        raise HTTPException(502, "ERC-20 swap unexpectedly requests native currency.")
    allowance = (data.get("issues") or {}).get("allowance") or {}
    spender = allowance.get("spender") or data.get("allowanceTarget")
    if spender is not None and (not isinstance(spender, str) or spender.lower() != ZEROX_ALLOWANCE_HOLDER):
        raise HTTPException(502, "Unexpected token allowance target.")
    return data

@app.get("/dex/price")
async def dex_price(chainId: int, sellToken: str, buyToken: str, sellAmount: str, taker: str, slippageBps: int = 100):
    if slippageBps < 10 or slippageBps > 300:
        raise HTTPException(422, "slippageBps must be between 10 and 300 (0.1% to 3%).")
    sell, buy = validate_dex_request(chainId, sellToken, buyToken, sellAmount, taker)
    data = await call_zerox("/swap/allowance-holder/price", {
        "chainId": str(chainId), "sellToken": sell, "buyToken": buy,
        "sellAmount": sellAmount, "taker": taker, "slippageBps": str(slippageBps),
    })
    if not isinstance(data, dict) or data.get("liquidityAvailable") is not True:
        raise HTTPException(422, "No executable liquidity is available for this pair and amount.")
    for field, expected in (("sellToken", sell), ("buyToken", buy)):
        actual = data.get(field)
        if not isinstance(actual, str) or actual.lower() != expected:
            raise HTTPException(502, "Price provider returned a mismatched token.")
    if str(data.get("sellAmount", "")) != sellAmount:
        raise HTTPException(502, "Price provider returned a mismatched sell amount.")
    out = str(data.get("buyAmount", ""))
    if not out.isascii() or not out.isdigit() or int(out) <= 0:
        raise HTTPException(502, "Price provider returned an invalid output amount.")
    return data

class DexQuoteRequest(BaseModel):
    model_config = {"extra": "forbid"}
    chainId: int = Field(ge=1, le=100000)
    sellToken: str = Field(min_length=42, max_length=42, pattern=r"^0x[0-9a-fA-F]{40}$")
    buyToken: str = Field(min_length=42, max_length=42, pattern=r"^0x[0-9a-fA-F]{40}$")
    sellAmount: str = Field(min_length=1, max_length=78, pattern=r"^[0-9]+$")
    taker: str = Field(min_length=42, max_length=42, pattern=r"^0x[0-9a-fA-F]{40}$")
    slippageBps: int = Field(default=100, ge=10, le=300)

@app.post("/dex/quote")
async def dex_quote(body: DexQuoteRequest):
    sell, buy = validate_dex_request(body.chainId, body.sellToken, body.buyToken, body.sellAmount, body.taker)
    data = await call_zerox("/swap/allowance-holder/quote", {
        "chainId": str(body.chainId), "sellToken": sell, "buyToken": buy,
        "sellAmount": body.sellAmount, "taker": body.taker, "slippageBps": str(body.slippageBps),
    })
    return validate_dex_quote_response(data, chain_id=body.chainId, sell_token=sell, buy_token=buy, sell_amount=body.sellAmount, taker=body.taker)
