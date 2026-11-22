import asyncio
import os
import hmac
import re
import sqlite3
import time
import json
import base64
import hashlib
from decimal import Decimal, InvalidOperation
from typing import Literal

import ccxt
import httpx
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from pydantic import SecretStr
from fastapi import FastAPI, Header, HTTPException, Request, WebSocket, WebSocketDisconnect, Depends
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from paper_engine import IdempotencyConflict, PaperMatchingEngine
from paper_ledger import (InsufficientPaperFunds, LedgerIdempotencyConflict, PaperLedger)
from paper_risk import PaperOrderGuard, PaperOrderLimitExceeded
from auth_service import AuthService, AuthenticationError, UsernameTaken
from market_stream import MarketStreamHub
from orderbook_depth import build_depth
from paper_orderbook_stream import PaperOrderBookHub, build_depth_delta

APP_NAME = "ZenCrypto Exchange Connector"
EXCHANGE_ID = os.getenv("EXCHANGE_ID", "binance").lower()
API_KEY = os.getenv("EXCHANGE_API_KEY", "")
API_SECRET = os.getenv("EXCHANGE_API_SECRET", "")
ADMIN_TOKEN = os.getenv("ZENCRYPTO_ADMIN_TOKEN", "")
SANDBOX = os.getenv("EXCHANGE_SANDBOX", "true").lower() == "true"
LIVE_TRADING_ENABLED = os.getenv("LIVE_TRADING_ENABLED", "false").lower() == "true"
LIVE_CUSTOMER_ORDER_ROUTING_ENABLED = os.getenv("LIVE_CUSTOMER_ORDER_ROUTING_ENABLED", "false").lower() == "true"
WALLET_CUSTODY_PROVIDER = os.getenv("WALLET_CUSTODY_PROVIDER", "").strip().lower()
WALLET_DEPOSITS_ENABLED = os.getenv("WALLET_DEPOSITS_ENABLED", "false").lower() == "true"
WALLET_WITHDRAWALS_ENABLED = os.getenv("WALLET_WITHDRAWALS_ENABLED", "false").lower() == "true"
BITGO_API_ENV = os.getenv("BITGO_API_ENV", "test").strip().lower()
BITGO_WEBHOOK_SECRET = os.getenv("BITGO_WEBHOOK_SECRET", "")
BITGO_ENTERPRISE_ID = os.getenv("BITGO_ENTERPRISE_ID", "")
BITGO_ACCESS_TOKEN = os.getenv("BITGO_ACCESS_TOKEN", "")
BITGO_TRANSFER_ADAPTER_READY = False  # Enable only after provider API and transfer verification tests pass.
PLATFORM_API_BASE_URL = os.getenv("PLATFORM_API_BASE_URL", "https://zencrypto-api.onrender.com").rstrip("/")
EXCHANGE_CREDENTIAL_ENCRYPTION_KEY = os.getenv("EXCHANGE_CREDENTIAL_ENCRYPTION_KEY", "")
CUSTOMER_PREVIEW_MAX_NOTIONAL_USDT = Decimal(os.getenv("CUSTOMER_PREVIEW_MAX_NOTIONAL_USDT", "250"))
CUSTOMER_PREVIEW_FEE_BUFFER_RATE = Decimal(os.getenv("CUSTOMER_PREVIEW_FEE_BUFFER_RATE", "0.001"))
ALLOWED_ORIGINS = [x.strip() for x in os.getenv(
    "ALLOWED_ORIGINS",
    "https://jacksonmariso02-crypto.github.io"
).split(",") if x.strip()]

app = FastAPI(title=APP_NAME, version="0.1.0", docs_url="/docs")
PAPER_ENGINE_DB_PATH = os.getenv("PAPER_ENGINE_DB_PATH", "/tmp/zencrypto-paper-engine.sqlite3")
PAPER_MAKER_FEE_BPS = os.getenv("PAPER_MAKER_FEE_BPS", "10")
PAPER_TAKER_FEE_BPS = os.getenv("PAPER_TAKER_FEE_BPS", "20")
PAPER_ENGINE = PaperMatchingEngine(
    PAPER_ENGINE_DB_PATH,
    maker_fee_bps=PAPER_MAKER_FEE_BPS,
    taker_fee_bps=PAPER_TAKER_FEE_BPS,
)
PAPER_LEDGER = PaperLedger(PAPER_ENGINE_DB_PATH)
PAPER_BOOK_STREAM_INTERVAL_SECONDS = max(
    0.25, float(os.getenv("PAPER_BOOK_STREAM_INTERVAL_SECONDS", "0.5"))
)


async def fetch_paper_orderbook_snapshot(symbol: str):
    return await run_in_threadpool(PAPER_ENGINE.orderbook_depth, symbol, 100)


PAPER_ORDERBOOK_HUB = PaperOrderBookHub(
    fetch_paper_orderbook_snapshot,
    interval_seconds=PAPER_BOOK_STREAM_INTERVAL_SECONDS,
)

PAPER_ORDER_GUARD = PaperOrderGuard(
    max_notional=os.getenv("PAPER_MAX_ORDER_NOTIONAL", "100000"),
    max_requests=int(os.getenv("PAPER_ORDER_RATE_LIMIT", "30")),
    window_seconds=int(os.getenv("PAPER_ORDER_RATE_WINDOW_SECONDS", "60")),
)
AUTH_TOKEN_SECRET = os.getenv("AUTH_TOKEN_SECRET", "")
AUTH_TOKEN_TTL_SECONDS = int(os.getenv("AUTH_TOKEN_TTL_SECONDS", "900"))
AUTH_SERVICE = AuthService(PAPER_ENGINE_DB_PATH, AUTH_TOKEN_SECRET, AUTH_TOKEN_TTL_SECONDS)

# Customer exchange credentials are stored separately from platform sessions.
# The browser submits keys once over HTTPS; only authenticated ciphertext is
# persisted. The encryption key must be supplied through the hosting secret
# manager and must never be committed to the repository.
def _vault_cipher():
    raw = EXCHANGE_CREDENTIAL_ENCRYPTION_KEY.strip()
    if not raw:
        raise HTTPException(503, "Customer credential vault is not configured.")
    if len(raw) < 32:
        raise HTTPException(503, "Customer credential vault key must contain at least 32 characters.")
    # Derive a fixed-size AES-256 key from the host-managed high-entropy secret.
    key = hashlib.sha256(raw.encode("utf-8")).digest()
    return AESGCM(key)


def _init_customer_credential_store():
    with sqlite3.connect(PAPER_ENGINE_DB_PATH, timeout=15) as db:
        db.execute("""
            CREATE TABLE IF NOT EXISTS customer_exchange_connections (
                user_id TEXT NOT NULL,
                exchange_id TEXT NOT NULL,
                encrypted_credentials BLOB NOT NULL,
                masked_api_key TEXT NOT NULL,
                permissions_json TEXT NOT NULL,
                status TEXT NOT NULL,
                connected_at INTEGER NOT NULL,
                last_verified_at INTEGER NOT NULL,
                PRIMARY KEY(user_id, exchange_id)
            )
        """)
        db.execute("""
            CREATE INDEX IF NOT EXISTS idx_customer_exchange_connections_user
            ON customer_exchange_connections(user_id, status)
        """)
        db.execute("""
            CREATE TABLE IF NOT EXISTS customer_order_requests (
                user_id TEXT NOT NULL,
                idempotency_key TEXT NOT NULL,
                request_hash TEXT NOT NULL,
                exchange_client_order_id TEXT NOT NULL,
                status TEXT NOT NULL,
                response_json TEXT,
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL,
                PRIMARY KEY(user_id, idempotency_key),
                UNIQUE(exchange_client_order_id)
            )
        """)
        db.execute("""
            CREATE INDEX IF NOT EXISTS idx_customer_order_requests_user_time
            ON customer_order_requests(user_id, created_at DESC)
        """)
        db.execute("""
            CREATE TABLE IF NOT EXISTS wallet_journals (
                journal_id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                asset TEXT NOT NULL,
                event_type TEXT NOT NULL,
                status TEXT NOT NULL,
                idempotency_key TEXT NOT NULL,
                provider_reference TEXT,
                created_at INTEGER NOT NULL,
                UNIQUE(user_id, idempotency_key)
            )
        """)
        db.execute("""
            CREATE TABLE IF NOT EXISTS wallet_ledger_entries (
                entry_id INTEGER PRIMARY KEY AUTOINCREMENT,
                journal_id TEXT NOT NULL,
                user_id TEXT NOT NULL,
                asset TEXT NOT NULL,
                account_code TEXT NOT NULL,
                direction TEXT NOT NULL CHECK(direction IN ('debit', 'credit')),
                amount TEXT NOT NULL,
                created_at INTEGER NOT NULL,
                FOREIGN KEY(journal_id) REFERENCES wallet_journals(journal_id)
            )
        """)
        db.execute("""
            CREATE INDEX IF NOT EXISTS idx_wallet_ledger_user_asset
            ON wallet_ledger_entries(user_id, asset, created_at DESC)
        """)
        db.execute("""
            CREATE TABLE IF NOT EXISTS customer_custody_wallets (
                user_id TEXT NOT NULL, provider TEXT NOT NULL, provider_wallet_id TEXT NOT NULL,
                asset TEXT NOT NULL, network TEXT NOT NULL, created_at INTEGER NOT NULL,
                status TEXT NOT NULL,
                PRIMARY KEY(user_id, provider, provider_wallet_id, asset, network)
            )
        """)
        db.execute("""
            CREATE TABLE IF NOT EXISTS customer_deposit_addresses (
                user_id TEXT NOT NULL, provider TEXT NOT NULL, provider_wallet_id TEXT NOT NULL,
                asset TEXT NOT NULL, network TEXT NOT NULL, address TEXT NOT NULL,
                address_tag TEXT, created_at INTEGER NOT NULL, status TEXT NOT NULL,
                UNIQUE(provider, network, address, address_tag)
            )
        """)
        db.execute("""
            CREATE TABLE IF NOT EXISTS custody_webhook_events (
                provider TEXT NOT NULL, event_id TEXT NOT NULL, payload_hash TEXT NOT NULL,
                payload_json TEXT NOT NULL, status TEXT NOT NULL, received_at INTEGER NOT NULL,
                processed_at INTEGER, PRIMARY KEY(provider, event_id)
            )
        """)
        db.execute("""
            CREATE TABLE IF NOT EXISTS customer_withdrawal_requests (
                request_id TEXT PRIMARY KEY, user_id TEXT NOT NULL, provider TEXT NOT NULL,
                asset TEXT NOT NULL, network TEXT NOT NULL, amount TEXT NOT NULL,
                destination_address TEXT NOT NULL, destination_tag TEXT, status TEXT NOT NULL,
                idempotency_key TEXT NOT NULL, created_at INTEGER NOT NULL,
                UNIQUE(user_id, idempotency_key)
            )
        """)


_init_customer_credential_store()


async def require_platform_user(authorization: str | None = Header(default=None)):
    token = (authorization or "").removeprefix("Bearer ").strip()
    if not token or len(token) > 4096:
        raise HTTPException(401, "Authentication required.", headers={"WWW-Authenticate": "Bearer"})
    try:
        async with httpx.AsyncClient(timeout=6.0) as client:
            response = await client.get(
                PLATFORM_API_BASE_URL + "/account/me",
                headers={"Authorization": "Bearer " + token},
            )
        if response.status_code != 200:
            raise HTTPException(401, "Platform session is invalid or expired.")
        user = response.json()
        user_id = user.get("id")
        if not isinstance(user_id, (int, str)) or not str(user_id):
            raise HTTPException(401, "Platform session could not be verified.")
        return {"id": str(user_id), "email": str(user.get("email", "")), "role": str(user.get("role", "user"))}
    except HTTPException:
        raise
    except (httpx.HTTPError, ValueError):
        raise HTTPException(503, "Unable to verify the platform session. Please retry.") from None


def _encrypt_customer_credentials(user_id: str, api_key: str, api_secret: str) -> bytes:
    cipher = _vault_cipher()
    nonce = os.urandom(12)
    aad = (user_id + ":binance:v1").encode("utf-8")
    payload = json.dumps({"api_key": api_key, "api_secret": api_secret}, separators=(",", ":")).encode("utf-8")
    return nonce + cipher.encrypt(nonce, payload, aad)


def _decrypt_customer_credentials(user_id: str, envelope: bytes) -> dict:
    cipher = _vault_cipher()
    nonce, ciphertext = envelope[:12], envelope[12:]
    aad = (user_id + ":binance:v1").encode("utf-8")
    try:
        return json.loads(cipher.decrypt(nonce, ciphertext, aad).decode("utf-8"))
    except Exception:
        raise HTTPException(503, "Stored exchange authorization could not be decrypted. Contact support.") from None


def _customer_connection(user_id: str):
    with sqlite3.connect(PAPER_ENGINE_DB_PATH, timeout=15) as db:
        db.row_factory = sqlite3.Row
        row = db.execute(
            """SELECT exchange_id, masked_api_key, permissions_json, status,
                      connected_at, last_verified_at
               FROM customer_exchange_connections
               WHERE user_id=? AND exchange_id='binance'""",
            (user_id,),
        ).fetchone()
    if not row:
        return {"connected": False, "exchange": "binance"}
    return {
        "connected": row["status"] == "connected",
        "exchange": row["exchange_id"],
        "masked_api_key": row["masked_api_key"],
        "permissions": json.loads(row["permissions_json"]),
        "status": row["status"],
        "connected_at": row["connected_at"],
        "last_verified_at": row["last_verified_at"],
        "live_order_routing_enabled": bool(LIVE_CUSTOMER_ORDER_ROUTING_ENABLED and LIVE_TRADING_ENABLED and not SANDBOX),
    }


def _verify_binance_customer_credentials(api_key: str, api_secret: str):
    # This deliberately targets the user's live Binance account only for
    # credential verification and permission inspection. No order is sent.
    client = ccxt.binance({
        "apiKey": api_key,
        "secret": api_secret,
        "enableRateLimit": True,
        "options": {"defaultType": "spot"},
    })
    try:
        permission_method = getattr(client, "privateGetAccountApiRestrictions", None)
        if permission_method is None:
            raise HTTPException(503, "This exchange connector cannot verify Binance API restrictions.")
        restrictions = permission_method()
        can_read = bool(restrictions.get("enableReading"))
        can_spot_trade = bool(restrictions.get("enableSpotAndMarginTrading"))
        can_withdraw = bool(restrictions.get("enableWithdrawals"))
        if not can_read:
            raise HTTPException(422, "The Binance API key must have read permission enabled.")
        if not can_spot_trade:
            raise HTTPException(422, "Enable Binance Spot & Margin Trading permission to connect this account.")
        if can_withdraw:
            raise HTTPException(422, "For safety, API keys with withdrawal permission are not accepted.")
        # Fetching balances confirms that the key is valid for account reads.
        balance = client.fetch_balance()
        permissions = {
            "read": can_read,
            "spot_trading": can_spot_trade,
            "withdrawals": can_withdraw,
            "margin_endpoints_used": False,
            "futures_endpoints_used": False,
        }
        return permissions, balance
    except HTTPException:
        raise
    except ccxt.BaseError:
        # Do not echo exchange exception text; some providers include request
        # metadata in errors. Never leak credentials into application logs.
        raise HTTPException(422, "Binance could not verify these API credentials. Check the key, secret, IP restrictions and account status.") from None
    finally:
        try:
            client.close()
        except Exception:
            pass


class CustomerExchangeConnectRequest(BaseModel):
    api_key: SecretStr = Field(min_length=16, max_length=256)
    api_secret: SecretStr = Field(min_length=16, max_length=256)

class CustomerOrderPreviewRequest(BaseModel):
    symbol: str = Field(min_length=3, max_length=24)
    side: Literal["buy", "sell"]
    order_type: Literal["market", "limit"] = "market"
    amount: Decimal = Field(gt=0, max_digits=24, decimal_places=12)
    price: Decimal | None = Field(default=None, gt=0, max_digits=24, decimal_places=12)


def _calculate_customer_order_preview(exchange, body: CustomerOrderPreviewRequest):
    """Read-only spot order preflight. This function never submits or persists an order."""
    try:
        symbol = normalize_symbol(body.symbol)
        markets = exchange.load_markets()
        market = markets.get(symbol)
        if not market or market.get("active") is False or market.get("spot") is not True:
            raise HTTPException(422, "This is not an active Binance Spot market.")
        if market.get("quote") != "USDT":
            raise HTTPException(422, "Order preview currently supports USDT-quoted Spot pairs only.")
        if body.order_type == "limit" and body.price is None:
            raise HTTPException(422, "A limit order requires a limit price.")
        if body.order_type == "market" and body.price is not None:
            raise HTTPException(422, "Do not provide a price for a market order.")

        amount_text = exchange.amount_to_precision(symbol, str(body.amount))
        amount = Decimal(amount_text)
        if amount <= 0 or amount != body.amount:
            raise HTTPException(422, "Order amount exceeds the market's supported precision.")

        ticker = exchange.fetch_ticker(symbol)
        reference_raw = body.price if body.order_type == "limit" else (
            ticker.get("ask") if body.side == "buy" else ticker.get("bid")
        )
        if reference_raw is None:
            reference_raw = ticker.get("last")
        if reference_raw is None:
            raise HTTPException(503, "A current reference price is unavailable.")
        reference_price = Decimal(str(reference_raw))
        if not reference_price.is_finite() or reference_price <= 0:
            raise HTTPException(503, "The exchange returned an invalid reference price.")

        price_text = exchange.price_to_precision(symbol, str(reference_price))
        reference_price = Decimal(price_text)
        if body.order_type == "limit" and reference_price != body.price:
            raise HTTPException(422, "Limit price exceeds the market's supported precision.")

        notional = amount * reference_price
        limits = market.get("limits") or {}
        amount_limits = limits.get("amount") or {}
        cost_limits = limits.get("cost") or {}
        min_amount = Decimal(str(amount_limits["min"])) if amount_limits.get("min") is not None else None
        max_amount = Decimal(str(amount_limits["max"])) if amount_limits.get("max") is not None else None
        min_cost = Decimal(str(cost_limits["min"])) if cost_limits.get("min") is not None else None
        max_cost = Decimal(str(cost_limits["max"])) if cost_limits.get("max") is not None else None
        if min_amount is not None and amount < min_amount:
            raise HTTPException(422, "Order amount is below Binance's minimum.")
        if max_amount is not None and amount > max_amount:
            raise HTTPException(422, "Order amount exceeds Binance's maximum.")
        if min_cost is not None and notional < min_cost:
            raise HTTPException(422, "Estimated order value is below Binance's minimum notional.")
        if max_cost is not None and notional > max_cost:
            raise HTTPException(422, "Estimated order value exceeds Binance's maximum notional.")
        if (not CUSTOMER_PREVIEW_MAX_NOTIONAL_USDT.is_finite()
                or CUSTOMER_PREVIEW_MAX_NOTIONAL_USDT <= 0
                or CUSTOMER_PREVIEW_MAX_NOTIONAL_USDT > Decimal("2500")):
            raise HTTPException(503, "Customer order preview risk limit is not configured safely.")
        if (not CUSTOMER_PREVIEW_FEE_BUFFER_RATE.is_finite()
                or CUSTOMER_PREVIEW_FEE_BUFFER_RATE < 0
                or CUSTOMER_PREVIEW_FEE_BUFFER_RATE > Decimal("0.05")):
            raise HTTPException(503, "Customer order preview fee buffer is not configured safely.")
        if notional > CUSTOMER_PREVIEW_MAX_NOTIONAL_USDT:
            raise HTTPException(422, "Order exceeds the current per-order preview risk limit of "
                                + format(CUSTOMER_PREVIEW_MAX_NOTIONAL_USDT, "f") + " USDT.")

        balances = exchange.fetch_balance()
        free = balances.get("free") or {}
        base_asset, quote_asset = market["base"], market["quote"]
        fee_buffer = notional * CUSTOMER_PREVIEW_FEE_BUFFER_RATE
        required = notional + fee_buffer if body.side == "buy" else amount
        asset = quote_asset if body.side == "buy" else base_asset
        available = Decimal(str(free.get(asset) or "0"))
        if not available.is_finite() or available < required:
            raise HTTPException(409, "Insufficient available " + asset + " balance for this order and fee buffer.")

        return {
            "exchange": "binance",
            "symbol": symbol,
            "side": body.side,
            "order_type": body.order_type,
            "amount": format(amount, "f"),
            "reference_price": format(reference_price, "f"),
            "estimated_notional_usdt": format(notional, "f"),
            "estimated_fee_buffer_usdt": format(fee_buffer, "f"),
            "estimated_required_balance": format(required, "f"),
            "required_asset": asset,
            "available_balance": format(available, "f"),
            "market_min_notional": format(min_cost, "f") if min_cost is not None else None,
            "per_order_preview_limit_usdt": format(CUSTOMER_PREVIEW_MAX_NOTIONAL_USDT, "f"),
            "price_source": "binance_live_ticker",
            "balance_source": "customer_binance_spot_account",
            "order_submitted": False,
            "can_submit": False,
            "execution_status": "PREVIEW_ONLY",
        }
    except HTTPException:
        raise
    except ccxt.BaseError:
        raise HTTPException(502, "Binance could not complete the read-only order preflight. No order was submitted.") from None
    except (InvalidOperation, ValueError, TypeError, KeyError):
        raise HTTPException(422, "Order preview inputs or exchange market data are invalid.") from None


@app.post("/api/v1/user/orders/preview")
async def preview_customer_order(
    body: CustomerOrderPreviewRequest,
    user=Depends(require_platform_user),
):
    with sqlite3.connect(PAPER_ENGINE_DB_PATH, timeout=15) as db:
        row = db.execute(
            """SELECT encrypted_credentials FROM customer_exchange_connections
               WHERE user_id=? AND exchange_id='binance' AND status='connected'""",
            (user["id"],),
        ).fetchone()
    if not row:
        raise HTTPException(404, "Connect a Binance account before requesting an order preview.")
    credentials = _decrypt_customer_credentials(user["id"], row[0])
    exchange = ccxt.binance({
        "apiKey": credentials["api_key"],
        "secret": credentials["api_secret"],
        "enableRateLimit": True,
        "options": {"defaultType": "spot"},
    })
    try:
        return await run_in_threadpool(_calculate_customer_order_preview, exchange, body)
    finally:
        await run_in_threadpool(exchange.close)


def _lookup_customer_order_request(user_id: str, idempotency_key: str, request_hash: str):
    with sqlite3.connect(PAPER_ENGINE_DB_PATH, timeout=15) as db:
        row = db.execute(
            """SELECT request_hash, status, response_json FROM customer_order_requests
               WHERE user_id=? AND idempotency_key=?""",
            (user_id, idempotency_key),
        ).fetchone()
    if not row:
        return None
    if not hmac.compare_digest(row[0], request_hash):
        raise HTTPException(409, "This idempotency key was already used for a different order request.")
    if row[1] == "SUBMITTED" and row[2]:
        response = json.loads(row[2])
        response["idempotent_replay"] = True
        return response
    raise HTTPException(409, "An order with this idempotency key is already processing or requires exchange reconciliation. Do not retry with a new key.")

def _claim_customer_order_request(user_id: str, idempotency_key: str, request_hash: str):
    """Atomically reserve an idempotency key before any exchange write."""
    client_order_id = "zc" + hashlib.sha256((user_id + ":" + idempotency_key).encode()).hexdigest()[:32]
    now = int(time.time())
    with sqlite3.connect(PAPER_ENGINE_DB_PATH, timeout=15, isolation_level=None) as db:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute(
            """SELECT request_hash, status, response_json
               FROM customer_order_requests WHERE user_id=? AND idempotency_key=?""",
            (user_id, idempotency_key),
        ).fetchone()
        if row:
            db.execute("COMMIT")
            if not hmac.compare_digest(row[0], request_hash):
                raise HTTPException(409, "This idempotency key was already used for a different order request.")
            if row[1] == "SUBMITTED" and row[2]:
                response = json.loads(row[2])
                response["idempotent_replay"] = True
                return None, response
            raise HTTPException(409, "An order with this idempotency key is already processing or requires exchange reconciliation. Do not retry with a new key.")
        db.execute(
            """INSERT INTO customer_order_requests
               (user_id, idempotency_key, request_hash, exchange_client_order_id,
                status, created_at, updated_at)
               VALUES (?, ?, ?, ?, 'SUBMITTING', ?, ?)""",
            (user_id, idempotency_key, request_hash, client_order_id, now, now),
        )
        db.execute("COMMIT")
    return client_order_id, None


def _finish_customer_order_request(user_id: str, idempotency_key: str, status: str, response: dict | None):
    with sqlite3.connect(PAPER_ENGINE_DB_PATH, timeout=15) as db:
        db.execute(
            """UPDATE customer_order_requests SET status=?, response_json=?, updated_at=?
               WHERE user_id=? AND idempotency_key=?""",
            (status, json.dumps(response, separators=(",", ":"), default=str) if response is not None else None,
             int(time.time()), user_id, idempotency_key),
        )


class CustomerOrderSubmitRequest(CustomerOrderPreviewRequest):
    pass


@app.post("/api/v1/user/orders")
async def submit_customer_order(
    body: CustomerOrderSubmitRequest,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    user=Depends(require_platform_user),
):
    if not LIVE_CUSTOMER_ORDER_ROUTING_ENABLED or not LIVE_TRADING_ENABLED or SANDBOX:
        raise HTTPException(403, "Customer live order routing is disabled. Read-only order previews remain available.")
    if not idempotency_key or not re.fullmatch(r"[A-Za-z0-9._:-]{16,128}", idempotency_key):
        raise HTTPException(400, "A unique Idempotency-Key header (16-128 safe characters) is required.")

    with sqlite3.connect(PAPER_ENGINE_DB_PATH, timeout=15) as db:
        row = db.execute(
            """SELECT encrypted_credentials, permissions_json FROM customer_exchange_connections
               WHERE user_id=? AND exchange_id='binance' AND status='connected'""",
            (user["id"],),
        ).fetchone()
    if not row:
        raise HTTPException(404, "Connect a Binance account before submitting an order.")
    permissions = json.loads(row[1])
    if not permissions.get("read") or not permissions.get("spot_trading") or permissions.get("withdrawals"):
        raise HTTPException(403, "The connected Binance key does not meet the required read/Spot/no-withdrawal permissions.")

    credentials = _decrypt_customer_credentials(user["id"], row[0])
    exchange = ccxt.binance({
        "apiKey": credentials["api_key"],
        "secret": credentials["api_secret"],
        "enableRateLimit": True,
        "options": {"defaultType": "spot"},
    })
    try:
        normalized_request = {
            "symbol": normalize_symbol(body.symbol), "side": body.side, "type": body.order_type,
            "amount": format(body.amount.normalize(), "f"),
            "price": format(body.price.normalize(), "f") if body.price is not None else None,
        }
        request_hash = hashlib.sha256(
            json.dumps(normalized_request, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        replay = _lookup_customer_order_request(user["id"], idempotency_key, request_hash)
        if replay is not None:
            return replay
        preview = await run_in_threadpool(_calculate_customer_order_preview, exchange, body)
        client_order_id, replay = _claim_customer_order_request(user["id"], idempotency_key, request_hash)
        if replay is not None:
            return replay

        params = {"newClientOrderId": client_order_id}
        price = preview["reference_price"] if body.order_type == "limit" else None
        try:
            order = await run_in_threadpool(
                exchange.create_order,
                preview["symbol"], body.order_type, body.side,
                float(Decimal(preview["amount"])),
                float(Decimal(price)) if price is not None else None,
                params,
            )
        except Exception:
            _finish_customer_order_request(user["id"], idempotency_key, "UNKNOWN", None)
            raise HTTPException(502, "The exchange response was not conclusive. Order status requires reconciliation; do not resubmit.") from None

        safe_order = {
            "exchange": "binance",
            "exchange_order_id": str(order.get("id", "")),
            "client_order_id": client_order_id,
            "symbol": preview["symbol"],
            "side": body.side,
            "order_type": body.order_type,
            "amount": str(order.get("amount", preview["amount"])),
            "price": str(order.get("price")) if order.get("price") is not None else None,
            "status": str(order.get("status", "unknown")),
            "filled": str(order.get("filled", "0")),
            "remaining": str(order.get("remaining")) if order.get("remaining") is not None else None,
            "cost": str(order.get("cost")) if order.get("cost") is not None else None,
            "submitted_at": int(time.time()),
            "execution_status": "SUBMITTED",
            "idempotent_replay": False,
        }
        _finish_customer_order_request(user["id"], idempotency_key, "SUBMITTED", safe_order)
        return safe_order
    finally:
        await run_in_threadpool(exchange.close)




def _customer_order_records(user_id: str, limit: int = 100):
    with sqlite3.connect(PAPER_ENGINE_DB_PATH, timeout=15) as db:
        rows = db.execute(
            """SELECT idempotency_key, status, response_json, created_at, updated_at
               FROM customer_order_requests WHERE user_id=?
               ORDER BY created_at DESC LIMIT ?""",
            (user_id, max(1, min(int(limit), 100))),
        ).fetchall()
    records = []
    for key, status, response_json, created_at, updated_at in rows:
        payload = json.loads(response_json) if response_json else None
        records.append({
            "idempotency_key": key, "request_status": status,
            "order": payload, "created_at": created_at, "updated_at": updated_at,
        })
    return records


def _find_customer_order(user_id: str, exchange_order_id: str):
    for record in _customer_order_records(user_id, 100):
        order = record.get("order") or {}
        if hmac.compare_digest(str(order.get("exchange_order_id", "")), str(exchange_order_id)):
            return record
    raise HTTPException(404, "Order was not found for this customer.")


def _persist_customer_order_update(user_id: str, idempotency_key: str, status: str, order: dict):
    with sqlite3.connect(PAPER_ENGINE_DB_PATH, timeout=15) as db:
        db.execute(
            """UPDATE customer_order_requests SET status=?, response_json=?, updated_at=?
               WHERE user_id=? AND idempotency_key=?""",
            (status, json.dumps(order, separators=(",", ":"), default=str),
             int(time.time()), user_id, idempotency_key),
        )


def _safe_exchange_order(order: dict, previous: dict):
    return {
        **previous,
        "exchange_order_id": str(order.get("id", previous.get("exchange_order_id", ""))),
        "symbol": str(order.get("symbol", previous.get("symbol", ""))),
        "side": str(order.get("side", previous.get("side", ""))),
        "order_type": str(order.get("type", previous.get("order_type", ""))),
        "amount": str(order.get("amount", previous.get("amount", "0"))),
        "price": str(order.get("price")) if order.get("price") is not None else previous.get("price"),
        "status": str(order.get("status", previous.get("status", "unknown"))),
        "filled": str(order.get("filled", previous.get("filled", "0"))),
        "remaining": str(order.get("remaining")) if order.get("remaining") is not None else previous.get("remaining"),
        "cost": str(order.get("cost")) if order.get("cost") is not None else previous.get("cost"),
        "fee": order.get("fee", previous.get("fee")),
        "last_synced_at": int(time.time()),
    }


@app.get("/api/v1/user/orders")
async def list_customer_orders(user=Depends(require_platform_user)):
    return {"orders": _customer_order_records(user["id"], 100)}


@app.post("/api/v1/user/orders/{exchange_order_id}/refresh")
async def refresh_customer_order(exchange_order_id: str, user=Depends(require_platform_user)):
    record = _find_customer_order(user["id"], exchange_order_id)
    previous = record.get("order") or {}
    if not previous.get("symbol") or not previous.get("exchange_order_id"):
        raise HTTPException(409, "This order has no confirmed exchange order ID and cannot yet be synchronized.")
    with sqlite3.connect(PAPER_ENGINE_DB_PATH, timeout=15) as db:
        row = db.execute(
            """SELECT encrypted_credentials FROM customer_exchange_connections
               WHERE user_id=? AND exchange_id='binance' AND status='connected' """,
            (user["id"],),
        ).fetchone()
    if not row:
        raise HTTPException(404, "Connect the same Binance account to synchronize this order.")
    credentials = _decrypt_customer_credentials(user["id"], row[0])
    exchange = ccxt.binance({
        "apiKey": credentials["api_key"], "secret": credentials["api_secret"],
        "enableRateLimit": True, "options": {"defaultType": "spot"},
    })
    try:
        current = await run_in_threadpool(exchange.fetch_order, exchange_order_id, previous["symbol"])
        safe = _safe_exchange_order(current, previous)
        _persist_customer_order_update(user["id"], record["idempotency_key"], "SUBMITTED", safe)
        return safe
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(502, "Binance could not confirm this order status. Retry synchronization later.") from None
    finally:
        await run_in_threadpool(exchange.close)


@app.delete("/api/v1/user/orders/{exchange_order_id}")
async def cancel_customer_order(exchange_order_id: str, user=Depends(require_platform_user)):
    record = _find_customer_order(user["id"], exchange_order_id)
    previous = record.get("order") or {}
    if not previous.get("symbol") or not previous.get("exchange_order_id"):
        raise HTTPException(409, "This order has no confirmed exchange order ID and cannot be canceled.")
    if str(previous.get("status", "")).lower() in {"closed", "canceled", "cancelled", "expired", "rejected"}:
        raise HTTPException(409, "This order is already in a terminal state and cannot be canceled.")
    with sqlite3.connect(PAPER_ENGINE_DB_PATH, timeout=15) as db:
        row = db.execute(
            """SELECT encrypted_credentials FROM customer_exchange_connections
               WHERE user_id=? AND exchange_id="binance" AND status="connected" """,
            (user["id"],),
        ).fetchone()
    if not row:
        raise HTTPException(404, "Connect the same Binance account to cancel this order.")
    credentials = _decrypt_customer_credentials(user["id"], row[0])
    exchange = ccxt.binance({
        "apiKey": credentials["api_key"], "secret": credentials["api_secret"],
        "enableRateLimit": True, "options": {"defaultType": "spot"},
    })
    try:
        canceled = await run_in_threadpool(exchange.cancel_order, exchange_order_id, previous["symbol"])
        safe = _safe_exchange_order(canceled, previous)
        _persist_customer_order_update(user["id"], record["idempotency_key"], "SUBMITTED", safe)
        return safe
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(502, "Binance could not confirm cancellation. Synchronize the order before attempting another action.") from None
    finally:
        await run_in_threadpool(exchange.close)

def _post_wallet_journal(user_id: str, asset: str, event_type: str, idempotency_key: str,
                        postings: list[dict], provider_reference: str | None = None):
    """Post a balanced, idempotent ledger journal after a trusted provider event is verified."""
    asset = str(asset).upper().strip()
    if not re.fullmatch(r"[A-Z0-9]{2,16}", asset):
        raise ValueError("Invalid wallet asset.")
    if not idempotency_key or len(idempotency_key) > 128:
        raise ValueError("A bounded idempotency key is required.")
    if len(postings) < 2:
        raise ValueError("A journal requires at least two postings.")
    debit_total = Decimal("0")
    credit_total = Decimal("0")
    normalized = []
    for posting in postings:
        direction = str(posting.get("direction", "")).lower()
        account_code = str(posting.get("account_code", "")).strip()
        amount = Decimal(str(posting.get("amount", "0")))
        if direction not in {"debit", "credit"} or not account_code or not amount.is_finite() or amount <= 0:
            raise ValueError("Wallet postings must have valid accounts, directions and positive finite amounts.")
        if direction == "debit":
            debit_total += amount
        else:
            credit_total += amount
        normalized.append((account_code, direction, format(amount, "f")))
    if debit_total != credit_total:
        raise ValueError("Wallet journal is not balanced.")
    now = int(time.time())
    journal_id = "wlt_" + hashlib.sha256((user_id + ":" + idempotency_key).encode()).hexdigest()[:32]
    with sqlite3.connect(PAPER_ENGINE_DB_PATH, timeout=15, isolation_level=None) as db:
        db.execute("BEGIN IMMEDIATE")
        existing = db.execute(
            "SELECT journal_id, asset, event_type, provider_reference FROM wallet_journals WHERE user_id=? AND idempotency_key=?",
            (user_id, idempotency_key),
        ).fetchone()
        if existing:
            existing_rows = db.execute(
                """SELECT account_code, direction, amount FROM wallet_ledger_entries
                   WHERE journal_id=? ORDER BY account_code, direction, amount""",
                (existing[0],),
            ).fetchall()
            existing_postings = sorted((row[0], row[1], format(Decimal(row[2]), "f")) for row in existing_rows)
            requested_postings = sorted(normalized)
            if (existing[1] != asset or existing[2] != event_type or
                    existing[3] != provider_reference or existing_postings != requested_postings):
                db.execute("ROLLBACK")
                raise ValueError("Wallet idempotency key was reused with different transaction details.")
            db.execute("COMMIT")
            return existing[0], False
        db.execute(
            """INSERT INTO wallet_journals
               (journal_id, user_id, asset, event_type, status, idempotency_key, provider_reference, created_at)
               VALUES (?, ?, ?, ?, 'posted', ?, ?, ?)""",
            (journal_id, user_id, asset, event_type, idempotency_key, provider_reference, now),
        )
        db.executemany(
            """INSERT INTO wallet_ledger_entries
               (journal_id, user_id, asset, account_code, direction, amount, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            [(journal_id, user_id, asset, account, direction, amount, now)
             for account, direction, amount in normalized],
        )
        db.execute("COMMIT")
    return journal_id, True


def _wallet_provider_status():
    credentials_configured = bool(WALLET_CUSTODY_PROVIDER == "bitgo" and BITGO_ENTERPRISE_ID and BITGO_ACCESS_TOKEN and BITGO_WEBHOOK_SECRET)
    configured = bool(credentials_configured and BITGO_TRANSFER_ADAPTER_READY)
    return {
        "custody_provider": "bitgo" if WALLET_CUSTODY_PROVIDER == "bitgo" else None,
        "provider_configured": credentials_configured,
        "provider_adapter_ready": BITGO_TRANSFER_ADAPTER_READY,
        "wallet_enabled": configured,
        "deposits_enabled": bool(configured and WALLET_DEPOSITS_ENABLED),
        "withdrawals_enabled": bool(configured and WALLET_WITHDRAWALS_ENABLED),
        "custody_model": "third_party_custodian",
        "message": "Configure an approved BitGo enterprise, API token and webhook secret; provider onboarding is not complete." if not credentials_configured else ("BitGo credentials are present, but the transfer adapter and provider verification workflow are not production-ready." if not BITGO_TRANSFER_ADAPTER_READY else "BitGo adapter is ready; funding remains subject to operational and compliance approval."),
    }


def _wallet_balances_for_user(user_id: str):
    with sqlite3.connect(PAPER_ENGINE_DB_PATH, timeout=15) as db:
        db.row_factory = sqlite3.Row
        rows = db.execute(
            "SELECT asset, account_code, direction, amount FROM wallet_ledger_entries WHERE user_id=?",
            (user_id,),
        ).fetchall()
    totals = {}
    for row in rows:
        if row["account_code"] != "customer_available":
            continue
        value = Decimal(row["amount"])
        totals[row["asset"]] = totals.get(row["asset"], Decimal("0")) + (
            value if row["direction"] == "credit" else -value
        )
    return [
        {"asset": asset, "available": format(amount, "f")}
        for asset, amount in sorted(totals.items()) if amount != 0
    ]


def _wallet_activity_for_user(user_id: str, limit: int = 100):
    with sqlite3.connect(PAPER_ENGINE_DB_PATH, timeout=15) as db:
        db.row_factory = sqlite3.Row
        rows = db.execute(
            """SELECT journal_id, asset, event_type, status, provider_reference, created_at
               FROM wallet_journals WHERE user_id=? ORDER BY created_at DESC LIMIT ?""",
            (user_id, max(1, min(int(limit), 100))),
        ).fetchall()
    return [dict(row) for row in rows]


@app.get("/api/v1/user/wallet")
async def customer_wallet_summary(user=Depends(require_platform_user)):
    return {
        **_wallet_provider_status(),
        "balances": _wallet_balances_for_user(user["id"]),
        "activity": _wallet_activity_for_user(user["id"], 50),
        "ledger_model": "double_entry",
        "source": "zencrypto_internal_wallet_ledger",
    }


@app.get("/api/v1/user/wallet/activity")
async def customer_wallet_activity(user=Depends(require_platform_user)):
    return {"activity": _wallet_activity_for_user(user["id"], 100)}


@app.post("/api/v1/user/wallet/deposit-intents")
async def create_customer_wallet_deposit_intent(user=Depends(require_platform_user)):
    status = _wallet_provider_status()
    if not status["provider_configured"] or not status["deposits_enabled"]:
        raise HTTPException(503, "Wallet deposits are unavailable: no approved custody provider and deposit integration are enabled. Do not send funds to any address.")
    raise HTTPException(503, "The custody provider deposit-address adapter is not implemented. No deposit address has been created.")


@app.post("/api/v1/user/wallet/withdrawal-requests")
async def create_customer_wallet_withdrawal_request(user=Depends(require_platform_user)):
    status = _wallet_provider_status()
    if not status["provider_configured"] or not status["withdrawals_enabled"]:
        raise HTTPException(503, "Wallet withdrawals are unavailable: no approved custody provider and withdrawal integration are enabled.")
    raise HTTPException(503, "The custody provider withdrawal adapter, MFA approval and transaction monitoring are not implemented. No withdrawal has been submitted.")


@app.get("/api/v1/user/wallet/deposit-addresses")
async def customer_wallet_deposit_addresses(user=Depends(require_platform_user)):
    with sqlite3.connect(PAPER_ENGINE_DB_PATH, timeout=15) as db:
        db.row_factory = sqlite3.Row
        rows = db.execute(
            """SELECT provider, asset, network, address, address_tag, created_at, status
               FROM customer_deposit_addresses WHERE user_id=? AND status="active" ORDER BY created_at DESC""",
            (user["id"],),
        ).fetchall()
    return {"addresses": [dict(row) for row in rows]}


@app.post("/api/v1/webhooks/bitgo")
async def receive_bitgo_webhook(request: Request):
    if not BITGO_WEBHOOK_SECRET:
        raise HTTPException(503, "BitGo webhook verification is not configured.")
    raw = await request.body()
    supplied = request.headers.get("x-signature-sha256", "").strip().lower()
    expected = hmac.new(BITGO_WEBHOOK_SECRET.encode("utf-8"), raw, hashlib.sha256).hexdigest()
    if not supplied or not hmac.compare_digest(supplied, expected):
        raise HTTPException(401, "Invalid BitGo webhook signature.")
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        raise HTTPException(400, "Webhook body must be valid JSON.") from None
    event_id = payload.get("eventId") or payload.get("event_id") or payload.get("id")
    if not isinstance(event_id, str) or not event_id.strip() or len(event_id) > 200:
        raise HTTPException(400, "Webhook event identifier is missing or invalid.")
    digest = hashlib.sha256(raw).hexdigest()
    now = int(time.time())
    with sqlite3.connect(PAPER_ENGINE_DB_PATH, timeout=15) as db:
        cursor = db.execute(
            """INSERT OR IGNORE INTO custody_webhook_events
               (provider, event_id, payload_hash, payload_json, status, received_at)
               VALUES ("bitgo", ?, ?, ?, "pending_provider_verification", ?)""",
            (event_id, digest, raw.decode("utf-8"), now),
        )
        inserted = cursor.rowcount == 1
    return {
        "accepted": True,
        "duplicate": not inserted,
        "event_id": event_id,
        "status": "pending_provider_verification" if inserted else "already_received",
        "credited": False,
        "message": "Webhook signature accepted. Verify the transfer directly with BitGo and confirm on-chain status before posting any customer ledger credit.",
    }


@app.get("/api/v1/user/exchange-connections")
async def list_customer_exchange_connections(user=Depends(require_platform_user)):
    return {"connections": [_customer_connection(user["id"])]}


@app.post("/api/v1/user/exchange-connections/binance")
async def connect_customer_binance(
    body: CustomerExchangeConnectRequest,
    request: Request,
    user=Depends(require_platform_user),
):
    try:
        AUTH_ATTEMPT_GUARD.check(user["id"], "1", "1")
    except PaperOrderLimitExceeded as exc:
        raise HTTPException(429, "Too many connection attempts. Please wait before retrying.",
                            headers={"Retry-After": str(exc.retry_after)}) from exc
    api_key = body.api_key.get_secret_value().strip()
    api_secret = body.api_secret.get_secret_value().strip()
    if not api_key or not api_secret:
        raise HTTPException(422, "Both Binance API key and secret are required.")
    permissions, _ = await run_in_threadpool(_verify_binance_customer_credentials, api_key, api_secret)
    envelope = _encrypt_customer_credentials(user["id"], api_key, api_secret)
    now = int(time.time())
    with sqlite3.connect(PAPER_ENGINE_DB_PATH, timeout=15) as db:
        db.execute(
            """INSERT INTO customer_exchange_connections
               (user_id, exchange_id, encrypted_credentials, masked_api_key,
                permissions_json, status, connected_at, last_verified_at)
               VALUES (?, 'binance', ?, ?, ?, 'connected', ?, ?)
               ON CONFLICT(user_id, exchange_id) DO UPDATE SET
                 encrypted_credentials=excluded.encrypted_credentials,
                 masked_api_key=excluded.masked_api_key,
                 permissions_json=excluded.permissions_json,
                 status='connected',
                 last_verified_at=excluded.last_verified_at""",
            (user["id"], envelope, "••••" + api_key[-4:],
             json.dumps(permissions, separators=(",", ":")), now, now),
        )
    return {
        "connected": True,
        "exchange": "binance",
        "masked_api_key": "••••" + api_key[-4:],
        "permissions": permissions,
        "live_order_routing_enabled": False,
        "message": "Binance account verified and encrypted. Customer order routing remains disabled pending production security and compliance approval.",
    }


@app.get("/api/v1/user/exchange-connections/binance/balances")
async def customer_binance_balances(user=Depends(require_platform_user)):
    with sqlite3.connect(PAPER_ENGINE_DB_PATH, timeout=15) as db:
        row = db.execute(
            """SELECT encrypted_credentials FROM customer_exchange_connections
               WHERE user_id=? AND exchange_id='binance' AND status='connected'""",
            (user["id"],),
        ).fetchone()
    if not row:
        raise HTTPException(404, "No connected Binance account was found.")
    credentials = _decrypt_customer_credentials(user["id"], row[0])
    client = ccxt.binance({
        "apiKey": credentials["api_key"], "secret": credentials["api_secret"],
        "enableRateLimit": True, "options": {"defaultType": "spot"},
    })
    try:
        balance = await run_in_threadpool(client.fetch_balance)
        rows = []
        for currency, amount in (balance.get("total") or {}).items():
            try:
                total = Decimal(str(amount or "0"))
            except Exception:
                continue
            if total > 0 and total.is_finite():
                rows.append({
                    "asset": str(currency),
                    "total": format(total, "f"),
                    "free": format(Decimal(str((balance.get("free") or {}).get(currency) or "0")), "f"),
                    "used": format(Decimal(str((balance.get("used") or {}).get(currency) or "0")), "f"),
                })
        return {"exchange": "binance", "balances": rows, "observed_at": int(time.time()),
                "source": "customer_authorized_exchange_account"}
    except ccxt.BaseError:
        raise HTTPException(502, "Unable to retrieve balances from the connected Binance account.") from None
    finally:
        await run_in_threadpool(client.close)


@app.delete("/api/v1/user/exchange-connections/binance")
async def disconnect_customer_binance(user=Depends(require_platform_user)):
    with sqlite3.connect(PAPER_ENGINE_DB_PATH, timeout=15) as db:
        cursor = db.execute(
            "DELETE FROM customer_exchange_connections WHERE user_id=? AND exchange_id='binance'",
            (user["id"],),
        )
    return {"disconnected": cursor.rowcount > 0, "exchange": "binance",
            "message": "Stored Binance API authorization has been deleted."}

AUTH_ATTEMPT_GUARD = PaperOrderGuard(
    max_notional="1", max_requests=int(os.getenv("AUTH_ATTEMPT_RATE_LIMIT", "10")),
    window_seconds=int(os.getenv("AUTH_ATTEMPT_RATE_WINDOW_SECONDS", "300")),
)

def require_user(authorization: str | None):
    supplied = (authorization or "").removeprefix("Bearer ").strip()
    try:
        return AUTH_SERVICE.authenticate(supplied)
    except AuthenticationError as exc:
        raise HTTPException(401, str(exc), headers={"WWW-Authenticate": "Bearer"}) from exc

def check_auth_attempt(request: Request):
    client_key = request.client.host if request.client else "unknown"
    try:
        AUTH_ATTEMPT_GUARD.check(client_key, "1", "1")
    except PaperOrderLimitExceeded as exc:
        raise HTTPException(429, str(exc), headers={"Retry-After": str(exc.retry_after)}) from exc

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=False,
    allow_methods=["GET", "POST", "DELETE"],
    allow_headers=["Authorization", "Content-Type"],
)

def exchange_client(private: bool = False):
    if EXCHANGE_ID != "binance":
        raise HTTPException(503, "Only Binance Spot is configured in this first connector.")
    config = {"enableRateLimit": True, "options": {"defaultType": "spot"}}
    if private:
        if not API_KEY or not API_SECRET:
            raise HTTPException(503, "Exchange API credentials are not configured on the server.")
        config.update({"apiKey": API_KEY, "secret": API_SECRET})
    client = ccxt.binance(config)
    if SANDBOX:
        client.set_sandbox_mode(True)
    return client

MARKET_STREAM_INTERVAL_SECONDS = max(
    2.0, float(os.getenv("MARKET_STREAM_INTERVAL_SECONDS", "2"))
)


async def fetch_external_market_snapshot(symbol: str):
    """Fetch one sanitized public snapshot from the configured external spot venue."""
    client = exchange_client()
    try:
        markets = await run_in_threadpool(client.load_markets)
        market = markets.get(symbol)
        if not market or not market.get("spot"):
            raise ValueError("External spot market is not available.")
        ticker_data = await run_in_threadpool(client.fetch_ticker, symbol)
        book_data = await run_in_threadpool(client.fetch_order_book, symbol, 20)
        ticker_fields = (
            "timestamp", "datetime", "last", "bid", "ask", "high", "low",
            "baseVolume", "quoteVolume", "change", "percentage",
        )
        return {
            "source": "external_exchange",
            "exchange": EXCHANGE_ID,
            "market_type": "spot",
            "symbol": symbol,
            "observed_at": time.time_ns() // 1_000_000,
            "ticker": {key: ticker_data.get(key) for key in ticker_fields},
            "orderbook": {
                "timestamp": book_data.get("timestamp"),
                "nonce": book_data.get("nonce"),
                "bids": book_data.get("bids", [])[:20],
                "asks": book_data.get("asks", [])[:20],
            },
            "depth": build_depth(
                symbol,
                book_data.get("bids", []),
                book_data.get("asks", []),
                limit=20,
                source="external_exchange",
            ),
        }
    finally:
        await run_in_threadpool(client.close)


MARKET_STREAM_HUB = MarketStreamHub(
    fetch_external_market_snapshot,
    interval_seconds=MARKET_STREAM_INTERVAL_SECONDS,
)


@app.on_event("shutdown")
async def close_market_streams():
    await MARKET_STREAM_HUB.close()
    await PAPER_ORDERBOOK_HUB.close()


@app.websocket("/ws/v1/markets/{symbol}")
async def stream_external_market(websocket: WebSocket, symbol: str):
    """Fan out a shared, rate-limited external ticker and order-book snapshot."""
    try:
        normalized = normalize_symbol(symbol)
    except HTTPException:
        await websocket.close(code=1008, reason="Invalid market symbol.")
        return

    await websocket.accept()
    queue = await MARKET_STREAM_HUB.subscribe(normalized)
    await websocket.send_json({
        "type": "ready",
        "channel": "external.market",
        "source": "external_exchange",
        "exchange": EXCHANGE_ID,
        "symbol": normalized,
        "poll_interval_seconds": MARKET_STREAM_INTERVAL_SECONDS,
        "sandbox": SANDBOX,
    })
    try:
        while True:
            event = await queue.get()
            await websocket.send_json(event)
    except WebSocketDisconnect:
        pass
    finally:
        await MARKET_STREAM_HUB.unsubscribe(normalized, queue)


@app.get("/api/v1/market-data/status")
async def market_data_status():
    return {
        "source": "external_exchange",
        "exchange": EXCHANGE_ID,
        "market_type": "spot",
        "transport": "websocket",
        "polling_interval_seconds": MARKET_STREAM_INTERVAL_SECONDS,
        "sandbox": SANDBOX,
        "execution_environment": "sandbox" if SANDBOX else "live_provider_configured",
        "live_order_placement_enabled": bool(LIVE_CUSTOMER_ORDER_ROUTING_ENABLED and LIVE_TRADING_ENABLED and not SANDBOX),
        "customer_order_routing_enabled": bool(LIVE_CUSTOMER_ORDER_ROUTING_ENABLED and LIVE_TRADING_ENABLED and not SANDBOX),
        "customer_account_linking_enabled": len(EXCHANGE_CREDENTIAL_ENCRYPTION_KEY) >= 32,
        "credential_vault_configured": len(EXCHANGE_CREDENTIAL_ENCRYPTION_KEY) >= 32,
        "customer_account_verification_environment": "live_exchange_read_only",
        "order_execution_environment": "sandbox_locked",
    }


def require_admin(authorization: str | None):
    if not ADMIN_TOKEN:
        raise HTTPException(503, "ZENCRYPTO_ADMIN_TOKEN is not configured.")
    supplied = (authorization or "").removeprefix("Bearer ").strip()
    if not hmac.compare_digest(supplied, ADMIN_TOKEN):
        raise HTTPException(401, "Unauthorized.")
    
def normalize_symbol(symbol: str) -> str:
    value = symbol.strip().upper().replace("-", "/")
    if "/" not in value and value.endswith("USDT"):
        value = value[:-4] + "/USDT"
    if not re.fullmatch(r"[A-Z0-9]{2,15}/[A-Z0-9]{2,15}", value):
        raise HTTPException(400, "Use a valid spot symbol such as BTC/USDT.")
    return value

@app.get("/health")
async def health():
    return {
        "status": "ok",
        "service": APP_NAME,
        "exchange": EXCHANGE_ID,
        "build_revision": os.getenv("RENDER_GIT_COMMIT", "source"),
        "sandbox": SANDBOX,
        "live_trading_enabled": False,
        "live_trading_configuration_requested": LIVE_TRADING_ENABLED,
        "customer_order_routing_enabled": False,
        "customer_credential_vault_configured": len(EXCHANGE_CREDENTIAL_ENCRYPTION_KEY) >= 32,
        "credentials_configured": bool(API_KEY and API_SECRET),
    }

def _readiness_checks():
    database_ok = False
    try:
        required_tables = {
            "paper_orders", "paper_trades", "paper_accounts",
            "paper_users", "ledger_transactions", "ledger_entries",
            "daily_asset_snapshots",
        }
        with sqlite3.connect(PAPER_ENGINE.db_path, timeout=2) as db:
            result = db.execute("PRAGMA quick_check").fetchone()
            present_tables = {
                row[0] for row in db.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
            database_ok = bool(result and result[0] == "ok") and required_tables.issubset(present_tables)
    except sqlite3.Error:
        database_ok = False

    auth_secret_ok = (
        len(AUTH_TOKEN_SECRET) >= 32
        and not AUTH_TOKEN_SECRET.startswith("REPLACE_WITH_")
    )
    admin_token_ok = len(ADMIN_TOKEN) >= 32
    sandbox_mode = SANDBOX is True
    live_trading_disabled = LIVE_TRADING_ENABLED is False
    credential_vault_ok = len(EXCHANGE_CREDENTIAL_ENCRYPTION_KEY) >= 32
    return {
        "database": database_ok,
        "user_auth_secret": auth_secret_ok,
        "admin_token": admin_token_ok,
        "sandbox_mode": sandbox_mode,
        "live_trading_disabled": live_trading_disabled,
        "customer_credential_vault": credential_vault_ok,
    }


@app.get("/ready")
async def readiness():
    checks = await run_in_threadpool(_readiness_checks)
    ready = all(checks.values())
    payload = {
        "status": "ready" if ready else "not_ready",
        "service": APP_NAME,
        "build_revision": os.getenv("RENDER_GIT_COMMIT", "source"),
        "checks": checks,
        "sandbox": SANDBOX,
        "live_trading_enabled": LIVE_TRADING_ENABLED,
    }
    if not ready:
        raise HTTPException(503, detail=payload)
    return payload


@app.get("/api/v1/markets/{symbol}/info")
async def market_info(symbol: str):
    client = exchange_client()
    try:
        markets = await run_in_threadpool(client.load_markets)
        normalized = normalize_symbol(symbol)
        market = markets.get(normalized)
        if not market or not market.get("spot"):
            raise HTTPException(404, "Spot market was not found.")
        return {
            "symbol": normalized,
            "base": market.get("base"),
            "quote": market.get("quote"),
            "active": market.get("active"),
            "spot": bool(market.get("spot")),
            "precision": market.get("precision", {}),
            "limits": market.get("limits", {}),
        }
    except HTTPException:
        raise
    except ccxt.BaseError as exc:
        raise HTTPException(502, f"Exchange market metadata error: {exc}") from exc
    finally:
        client.close()

@app.get("/api/v1/markets/{symbol}/ticker")
async def ticker(symbol: str):
    client = exchange_client()
    try:
        return await run_in_threadpool(client.fetch_ticker, normalize_symbol(symbol))
    except ccxt.BaseError as exc:
        raise HTTPException(502, f"Exchange market-data error: {exc}") from exc
    finally:
        client.close()

@app.get("/api/v1/markets/{symbol}/orderbook")
async def orderbook(symbol: str, limit: int = 20):
    if limit not in (5, 10, 20, 50, 100):
        raise HTTPException(400, "limit must be 5, 10, 20, 50 or 100.")
    client = exchange_client()
    try:
        return await run_in_threadpool(client.fetch_order_book, normalize_symbol(symbol), limit)
    except ccxt.BaseError as exc:
        raise HTTPException(502, f"Exchange order-book error: {exc}") from exc
    finally:
        client.close()

@app.get("/api/v1/markets/{symbol}/depth")
async def external_market_depth(symbol: str, limit: int = 20):
    if limit not in (5, 10, 20, 50, 100):
        raise HTTPException(400, "limit must be 5, 10, 20, 50 or 100.")
    normalized = normalize_symbol(symbol)
    client = exchange_client()
    try:
        book = await run_in_threadpool(client.fetch_order_book, normalized, limit)
        return build_depth(
            normalized,
            book.get("bids", []),
            book.get("asks", []),
            limit=limit,
            source="external_exchange",
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except ccxt.BaseError as exc:
        raise HTTPException(502, f"Exchange depth error: {exc}") from exc
    finally:
        await run_in_threadpool(client.close)


@app.get("/api/v1/markets/{symbol}/trades")
async def trades(symbol: str, limit: int = 50):
    if not 1 <= limit <= 500:
        raise HTTPException(400, "limit must be between 1 and 500.")
    client = exchange_client()
    try:
        return await run_in_threadpool(client.fetch_trades, normalize_symbol(symbol), None, limit)
    except ccxt.BaseError as exc:
        raise HTTPException(502, f"Exchange trades error: {exc}") from exc
    finally:
        client.close()

@app.get("/api/v1/markets/{symbol}/candles")
async def candles(symbol: str, timeframe: str = "1h", limit: int = 100):
    allowed = {"1m", "5m", "15m", "30m", "1h", "4h", "1d", "1w"}
    if timeframe not in allowed:
        raise HTTPException(400, "Unsupported candle timeframe.")
    if not 1 <= limit <= 1000:
        raise HTTPException(400, "limit must be between 1 and 1000.")
    client = exchange_client()
    try:
        rows = await run_in_threadpool(
            client.fetch_ohlcv, normalize_symbol(symbol), timeframe, None, limit
        )
        return [
            {"timestamp": r[0], "open": r[1], "high": r[2], "low": r[3],
             "close": r[4], "volume": r[5]}
            for r in rows
        ]
    except ccxt.BaseError as exc:
        raise HTTPException(502, f"Exchange candle error: {exc}") from exc
    finally:
        client.close()

@app.get("/api/v1/account/balance")
async def balance(authorization: str | None = Header(default=None)):
    require_admin(authorization)
    client = exchange_client(private=True)
    try:
        return await run_in_threadpool(client.fetch_balance)
    except ccxt.BaseError as exc:
        raise HTTPException(502, f"Exchange account error: {exc}") from exc
    finally:
        client.close()

class OrderRequest(BaseModel):
    symbol: str = Field(min_length=5, max_length=24)
    side: Literal["buy", "sell"]
    type: Literal["limit", "market"]
    amount: Decimal = Field(gt=0)
    price: Decimal | None = Field(default=None, gt=0)

@app.post("/api/v1/orders")
async def create_order(body: OrderRequest, authorization: str | None = Header(default=None)):
    require_admin(authorization)
    if body.type == "limit" and body.price is None:
        raise HTTPException(400, "A limit order requires a price.")
    if body.type == "market" and body.price is not None:
        raise HTTPException(400, "Do not send a price with a market order.")
    if not body.amount.is_finite() or (body.price is not None and not body.price.is_finite()):
        raise HTTPException(422, "Order amount and price must be finite decimal values.")
    # This connector currently uses one platform-level API credential and
    # an administrator-only route. It is not a customer account execution
    # service. Never allow a deployment flag to turn this route into live
    # customer-money trading; per-user authorization, custody, compliance,
    # risk and reconciliation controls must be implemented and reviewed first.
    if not SANDBOX:
        raise HTTPException(403, "Live customer order routing is not implemented. The exchange connector must remain in sandbox mode.")
    if LIVE_TRADING_ENABLED:
        raise HTTPException(403, "Live customer order routing is not supported by this connector. Keep LIVE_TRADING_ENABLED=false.")
    client = exchange_client(private=True)
    try:
        markets = await run_in_threadpool(client.load_markets)
        symbol = normalize_symbol(body.symbol)
        if symbol not in markets or not markets[symbol].get("spot"):
            raise HTTPException(400, "This symbol is not an available spot market.")
        market = markets[symbol]
        try:
            amount_text = client.amount_to_precision(symbol, str(body.amount))
            amount_decimal = Decimal(amount_text)
            if amount_decimal != body.amount:
                raise HTTPException(422, "Order amount exceeds the precision supported by this market.")
            amount_limits = market.get("limits", {}).get("amount", {})
            minimum = amount_limits.get("min")
            maximum = amount_limits.get("max")
            if minimum is not None and amount_decimal < Decimal(str(minimum)):
                raise HTTPException(422, "Order amount is below the market minimum.")
            if maximum is not None and amount_decimal > Decimal(str(maximum)):
                raise HTTPException(422, "Order amount exceeds the market maximum.")
            amount = float(amount_decimal)
            price = None
            if body.price is not None:
                price_text = client.price_to_precision(symbol, str(body.price))
                precise_price = Decimal(price_text)
                if precise_price != body.price:
                    raise HTTPException(422, "Limit price exceeds the precision supported by this market.")
                price_limits = market.get("limits", {}).get("price", {})
                price_min = price_limits.get("min")
                price_max = price_limits.get("max")
                if price_min is not None and precise_price < Decimal(str(price_min)):
                    raise HTTPException(422, "Limit price is below the market minimum.")
                if price_max is not None and precise_price > Decimal(str(price_max)):
                    raise HTTPException(422, "Limit price exceeds the market maximum.")
                price = float(precise_price)
                cost_limits = market.get("limits", {}).get("cost", {})
                notional = amount_decimal * precise_price
                cost_min = cost_limits.get("min")
                cost_max = cost_limits.get("max")
                if cost_min is not None and notional < Decimal(str(cost_min)):
                    raise HTTPException(422, "Limit order notional is below the market minimum.")
                if cost_max is not None and notional > Decimal(str(cost_max)):
                    raise HTTPException(422, "Limit order notional exceeds the market maximum.")
            else:
                # Market orders have no client-supplied price. Estimate notional
                # from the executable side of the live book and reject orders
                # that are clearly outside exchange cost limits before submission.
                ticker = await run_in_threadpool(client.fetch_ticker, symbol)
                reference = ticker.get("ask") if body.side == "buy" else ticker.get("bid")
                if reference is None:
                    reference = ticker.get("last")
                try:
                    reference_price = Decimal(str(reference))
                except Exception as exc:
                    raise HTTPException(502, "Exchange did not provide a usable market reference price.") from exc
                if not reference_price.is_finite() or reference_price <= 0:
                    raise HTTPException(502, "Exchange did not provide a usable market reference price.")
                cost_limits = market.get("limits", {}).get("cost", {})
                estimated_notional = amount_decimal * reference_price
                cost_min = cost_limits.get("min")
                cost_max = cost_limits.get("max")
                if cost_min is not None and estimated_notional < Decimal(str(cost_min)):
                    raise HTTPException(422, "Estimated market order notional is below the market minimum.")
                if cost_max is not None and estimated_notional > Decimal(str(cost_max)):
                    raise HTTPException(422, "Estimated market order notional exceeds the market maximum.")
        except HTTPException:
            raise
        except (ccxt.BaseError, ArithmeticError, ValueError) as exc:
            raise HTTPException(422, f"Order precision validation failed: {exc}") from exc
        order = await run_in_threadpool(
            client.create_order, symbol, body.type, body.side, amount, price
        )
        return order
    except HTTPException:
        raise
    except ccxt.BaseError as exc:
        raise HTTPException(502, f"Exchange order rejected: {exc}") from exc
    finally:
        client.close()

@app.get("/api/v1/orders")
async def list_orders(symbol: str | None = None, authorization: str | None = Header(default=None)):
    require_admin(authorization)
    client = exchange_client(private=True)
    try:
        return await run_in_threadpool(
            client.fetch_open_orders, normalize_symbol(symbol) if symbol else None
        )
    except ccxt.BaseError as exc:
        raise HTTPException(502, f"Exchange order query error: {exc}") from exc
    finally:
        client.close()

@app.delete("/api/v1/orders/{order_id}")
async def cancel_order(order_id: str, symbol: str, authorization: str | None = Header(default=None)):
    require_admin(authorization)
    client = exchange_client(private=True)
    try:
        return await run_in_threadpool(client.cancel_order, order_id, normalize_symbol(symbol))
    except ccxt.BaseError as exc:
        raise HTTPException(502, f"Exchange cancel error: {exc}") from exc
    finally:
        client.close()


# Paper-only internal matching endpoints. These never submit orders to CCXT.
class PaperLimitOrderRequest(BaseModel):
    symbol: str = Field(min_length=5, max_length=24)
    side: Literal["buy", "sell"]
    quantity: Decimal = Field(gt=0)
    price: Decimal = Field(gt=0)
    client_order_id: str = Field(min_length=8, max_length=64)


@app.get("/api/v1/markets")
async def list_enabled_markets():
    """Public market metadata used by clients to format and validate order entry."""
    return await run_in_threadpool(PAPER_ENGINE.market_rules.list_markets)


@app.get("/api/v1/markets/{symbol:path}")
async def get_enabled_market(symbol: str):
    normalized = normalize_symbol(symbol)
    market = next(
        (item for item in PAPER_ENGINE.market_rules.list_markets()
         if item["symbol"] == normalized),
        None,
    )
    if market is None:
        raise HTTPException(404, "Market not found or trading is disabled.")
    return market


@app.get("/api/v1/paper/markets/{symbol}/candles")
async def paper_market_candles(
    symbol: str,
    timeframe: str = "1h",
    limit: int = 100,
    since: int | None = None,
):
    """OHLCV candles derived only from ZenCrypto paper-engine fills."""
    try:
        return await run_in_threadpool(
            PAPER_ENGINE.list_candles, normalize_symbol(symbol), timeframe, limit, since
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.post("/api/v1/paper/orders")
async def create_paper_order(
    body: PaperLimitOrderRequest,
    request: Request,
    authorization: str | None = Header(default=None),
):
    require_admin(authorization)
    try:
        PAPER_ORDER_GUARD.check(
            request.client.host if request.client else "unknown",
            body.quantity, body.price,
        )
    except PaperOrderLimitExceeded as exc:
        raise HTTPException(
            429 if "rate limit" in str(exc).lower() else 422,
            str(exc),
            headers={"Retry-After": str(exc.retry_after)},
        ) from exc
    try:
        return await run_in_threadpool(
            PAPER_ENGINE.place_limit_order,
            normalize_symbol(body.symbol),
            body.side,
            body.quantity,
            body.price,
            body.client_order_id,
        )
    except IdempotencyConflict as exc:
        raise HTTPException(409, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@app.get("/api/v1/paper/orders")
async def list_paper_orders(
    symbol: str | None = None,
    limit: int = 100,
    authorization: str | None = Header(default=None),
):
    require_admin(authorization)
    if not 1 <= limit <= 500:
        raise HTTPException(400, "limit must be between 1 and 500.")
    normalized = normalize_symbol(symbol) if symbol else None
    return await run_in_threadpool(PAPER_ENGINE.list_orders, normalized, limit)


@app.get("/api/v1/paper/orderbook/{symbol}")
async def paper_orderbook(
    symbol: str,
    limit: int = 50,
    authorization: str | None = Header(default=None),
):
    require_admin(authorization)
    if not 1 <= limit <= 100:
        raise HTTPException(400, "limit must be between 1 and 100.")
    return await run_in_threadpool(PAPER_ENGINE.orderbook, normalize_symbol(symbol), limit)



@app.get("/api/v1/paper/orderbook/{symbol}/depth")
async def paper_orderbook_depth(
    symbol: str,
    limit: int = 50,
    authorization: str | None = Header(default=None),
):
    require_admin(authorization)
    if not 1 <= limit <= 100:
        raise HTTPException(400, "limit must be between 1 and 100.")
    try:
        return await run_in_threadpool(
            PAPER_ENGINE.orderbook_depth, normalize_symbol(symbol), limit
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.get("/api/v1/paper/fees")
async def paper_platform_fee_balances(
    authorization: str | None = Header(default=None),
):
    require_admin(authorization)
    return await run_in_threadpool(PAPER_LEDGER.get_fee_balances)

@app.get("/api/v1/paper/trades")
async def list_paper_trades(
    symbol: str | None = None,
    limit: int = 100,
    authorization: str | None = Header(default=None),
):
    require_admin(authorization)
    if not 1 <= limit <= 500:
        raise HTTPException(400, "limit must be between 1 and 500.")
    normalized = normalize_symbol(symbol) if symbol else None
    return await run_in_threadpool(PAPER_ENGINE.list_trades, normalized, limit)


@app.websocket("/ws/v1/paper/orderbook/{symbol}")
async def stream_paper_orderbook(websocket: WebSocket, symbol: str):
    """Stream a full paper depth snapshot followed by price-level deltas."""
    try:
        normalized = normalize_symbol(symbol)
    except HTTPException:
        await websocket.close(code=1008, reason="Invalid market symbol.")
        return

    await websocket.accept()
    queue = await PAPER_ORDERBOOK_HUB.subscribe(normalized)
    await websocket.send_json({
        "type": "ready",
        "channel": "paper.orderbook",
        "source": "zencrypto_paper",
        "symbol": normalized,
        "poll_interval_seconds": PAPER_BOOK_STREAM_INTERVAL_SECONDS,
        "delta_quantity_zero_means": "remove_level",
    })
    previous = None
    sequence = 0
    try:
        while True:
            snapshot = await queue.get()
            if snapshot.get("type") == "error":
                await websocket.send_json(snapshot)
                continue
            sequence += 1
            if previous is None:
                event = {
                    "type": "depth_snapshot",
                    "source": "zencrypto_paper",
                    "symbol": normalized,
                    "sequence": sequence,
                    "data": snapshot,
                }
            else:
                event = build_depth_delta(previous, snapshot)
                event["sequence"] = sequence
                event["previous_sequence"] = sequence - 1
            await websocket.send_json(event)
            previous = snapshot
    except WebSocketDisconnect:
        pass
    finally:
        await PAPER_ORDERBOOK_HUB.unsubscribe(normalized, queue)


@app.websocket("/ws/v1/paper/trades")
async def stream_paper_trades(websocket: WebSocket):
    """Stream public paper fills with resumable, monotonically increasing trade IDs."""
    raw_symbol = websocket.query_params.get("symbol")
    try:
        symbol = normalize_symbol(raw_symbol) if raw_symbol else None
        raw_cursor = websocket.query_params.get("since_id")
        if raw_cursor is not None:
            cursor = int(raw_cursor)
            if cursor < 0:
                raise ValueError("since_id must be a non-negative integer.")
        else:
            cursor = await run_in_threadpool(PAPER_ENGINE.latest_trade_id, symbol)
    except (HTTPException, ValueError):
        await websocket.close(code=1008, reason="Invalid symbol or since_id.")
        return

    await websocket.accept()
    await websocket.send_json({
        "type": "ready",
        "channel": "paper.trades",
        "symbol": symbol,
        "cursor": cursor,
        "timestamp_unit": "unix_nanoseconds",
    })
    try:
        while True:
            batch = await run_in_threadpool(
                PAPER_ENGINE.list_trades_after, cursor, symbol, 500
            )
            for trade in batch:
                await websocket.send_json({
                    "type": "trade",
                    "data": trade,
                })
                cursor = int(trade["id"])
            if not batch:
                await asyncio.sleep(0.5)
    except WebSocketDisconnect:
        return


@app.delete("/api/v1/paper/orders/{order_id}")
async def cancel_paper_order(order_id: str, authorization: str | None = Header(default=None)):
    require_admin(authorization)
    try:
        return await run_in_threadpool(PAPER_ENGINE.cancel_order, order_id)
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


# Double-entry paper ledger. These endpoints only create simulated balances.
class PaperAccountRequest(BaseModel):
    account_id: str = Field(min_length=3, max_length=40)


class PaperCreditRequest(BaseModel):
    asset: str = Field(min_length=2, max_length=12)
    amount: Decimal = Field(gt=0)
    idempotency_key: str = Field(min_length=8, max_length=80)


class PaperBucketTransferRequest(BaseModel):
    asset: str = Field(min_length=2, max_length=12)
    amount: Decimal = Field(gt=0)
    from_bucket: Literal["available", "locked"]
    to_bucket: Literal["available", "locked"]
    idempotency_key: str = Field(min_length=8, max_length=80)
    memo: str = Field(default="", max_length=200)


@app.post("/api/v1/paper/accounts")
async def create_paper_account(
    body: PaperAccountRequest,
    authorization: str | None = Header(default=None),
):
    require_admin(authorization)
    try:
        return await run_in_threadpool(PAPER_LEDGER.create_account, body.account_id)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@app.post("/api/v1/paper/accounts/{account_id}/credit")
async def credit_paper_account(
    account_id: str,
    body: PaperCreditRequest,
    authorization: str | None = Header(default=None),
):
    require_admin(authorization)
    try:
        return await run_in_threadpool(
            PAPER_LEDGER.credit_paper_funds,
            account_id, body.asset, body.amount, body.idempotency_key,
        )
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc
    except LedgerIdempotencyConflict as exc:
        raise HTTPException(409, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@app.post("/api/v1/paper/accounts/{account_id}/orders")
async def create_account_paper_order(
    account_id: str,
    body: PaperLimitOrderRequest,
    request: Request,
    authorization: str | None = Header(default=None),
):
    require_admin(authorization)
    try:
        PAPER_ORDER_GUARD.check(
            request.client.host if request.client else "unknown",
            body.quantity, body.price,
        )
    except PaperOrderLimitExceeded as exc:
        raise HTTPException(
            429 if "rate limit" in str(exc).lower() else 422,
            str(exc),
            headers={"Retry-After": str(exc.retry_after)},
        ) from exc
    try:
        return await run_in_threadpool(
            PAPER_ENGINE.place_limit_order,
            normalize_symbol(body.symbol),
            body.side,
            body.quantity,
            body.price,
            body.client_order_id,
            account_id,
        )
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc
    except InsufficientPaperFunds as exc:
        raise HTTPException(409, str(exc)) from exc
    except IdempotencyConflict as exc:
        raise HTTPException(409, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@app.get("/api/v1/paper/accounts/{account_id}/orders")
async def list_account_paper_orders(
    account_id: str,
    symbol: str | None = None,
    limit: int = 100,
    authorization: str | None = Header(default=None),
):
    require_admin(authorization)
    if not 1 <= limit <= 500:
        raise HTTPException(400, "limit must be between 1 and 500.")
    try:
        normalized = normalize_symbol(symbol) if symbol else None
        return await run_in_threadpool(
            PAPER_ENGINE.list_orders, normalized, limit, account_id
        )
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@app.delete("/api/v1/paper/accounts/{account_id}/orders/{order_id}")
async def cancel_account_paper_order(
    account_id: str,
    order_id: str,
    authorization: str | None = Header(default=None),
):
    require_admin(authorization)
    try:
        return await run_in_threadpool(PAPER_ENGINE.cancel_order, order_id, account_id)
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


@app.get("/api/v1/paper/accounts/{account_id}/balances")
async def paper_account_balances(
    account_id: str,
    authorization: str | None = Header(default=None),
):
    require_admin(authorization)
    try:
        return await run_in_threadpool(PAPER_LEDGER.get_balances, account_id)
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@app.post("/api/v1/paper/admin/ledger/daily-snapshots")
async def capture_daily_paper_snapshots(
    as_of_date: str | None = None,
    authorization: str | None = Header(default=None),
):
    """Capture today's immutable UTC paper-asset snapshot; admin only."""
    require_admin(authorization)
    try:
        return await run_in_threadpool(
            PAPER_LEDGER.capture_daily_snapshots, as_of_date
        )
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@app.get("/api/v1/paper/admin/ledger/daily-snapshots")
async def list_daily_paper_snapshots(
    account_id: str | None = None,
    limit: int = 100,
    authorization: str | None = Header(default=None),
):
    """List immutable daily paper-asset snapshots; admin only."""
    require_admin(authorization)
    try:
        return await run_in_threadpool(
            PAPER_LEDGER.list_daily_snapshots, account_id, limit
        )
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.get("/api/v1/paper/admin/ledger/snapshot-comparison")
async def compare_paper_asset_snapshots(
    account_id: str,
    from_date: str,
    to_date: str,
    asset: str | None = None,
    authorization: str | None = Header(default=None),
):
    """Admin-only exact-decimal comparison of two daily paper asset snapshots."""
    require_admin(authorization)
    try:
        return await run_in_threadpool(
            PAPER_LEDGER.compare_daily_snapshots,
            account_id, from_date, to_date, asset,
        )
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@app.get("/api/v1/paper/admin/orders/funds-reconciliation")
async def paper_order_funds_reconciliation(
    authorization: str | None = Header(default=None),
):
    """Admin-only audit linking account order reserves, fills and ledger postings."""
    require_admin(authorization)
    return await run_in_threadpool(PAPER_ENGINE.audit_order_funds_integrity)


@app.get("/api/v1/paper/admin/orders/reconciliation")
async def paper_order_trade_reconciliation(
    authorization: str | None = Header(default=None),
):
    """Admin-only, read-only reconciliation of paper orders and trade fills."""
    require_admin(authorization)
    return await run_in_threadpool(PAPER_ENGINE.audit_order_integrity)


@app.get("/api/v1/paper/admin/finance-overview")
async def paper_admin_finance_overview(
    authorization: str | None = Header(default=None),
):
    """Admin-only aggregate view of paper balances, fees, ledger and snapshots."""
    require_admin(authorization)
    return await run_in_threadpool(PAPER_LEDGER.admin_finance_overview)


@app.get("/api/v1/paper/admin/ledger/reconciliation")
async def paper_ledger_reconciliation(
    authorization: str | None = Header(default=None),
):
    """Admin-only, read-only reconciliation of simulated account ledger integrity."""
    require_admin(authorization)
    return await run_in_threadpool(PAPER_LEDGER.audit_integrity)


@app.get("/api/v1/paper/accounts/{account_id}/ledger")
async def paper_account_ledger(
    account_id: str,
    limit: int = 100,
    authorization: str | None = Header(default=None),
):
    require_admin(authorization)
    try:
        return await run_in_threadpool(PAPER_LEDGER.list_entries, account_id, limit)
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.post("/api/v1/paper/accounts/{account_id}/bucket-transfer")
async def transfer_paper_bucket(
    account_id: str,
    body: PaperBucketTransferRequest,
    authorization: str | None = Header(default=None),
):
    require_admin(authorization)
    try:
        return await run_in_threadpool(
            PAPER_LEDGER.transfer,
            account_id, body.asset, body.amount, body.from_bucket, body.to_bucket,
            body.idempotency_key, body.memo,
        )
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc
    except InsufficientPaperFunds as exc:
        raise HTTPException(409, str(exc)) from exc
    except LedgerIdempotencyConflict as exc:
        raise HTTPException(409, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


# End-user paper account API. User identity and account ownership come only
# from the signed access token; clients cannot select another account_id.
class RegisterRequest(BaseModel):
    username: str = Field(min_length=3, max_length=32)
    password: str = Field(min_length=12, max_length=128)

class LoginRequest(BaseModel):
    username: str = Field(min_length=3, max_length=32)
    password: str = Field(min_length=1, max_length=128)

@app.post("/api/v1/auth/register")
async def register_user(body: RegisterRequest, request: Request):
    check_auth_attempt(request)
    try:
        return await run_in_threadpool(AUTH_SERVICE.register, body.username, body.password)
    except UsernameTaken as exc:
        raise HTTPException(409, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(503, str(exc)) from exc

@app.post("/api/v1/auth/login")
async def login_user(body: LoginRequest, request: Request):
    check_auth_attempt(request)
    try:
        return await run_in_threadpool(AUTH_SERVICE.login, body.username, body.password)
    except AuthenticationError as exc:
        raise HTTPException(401, str(exc), headers={"WWW-Authenticate": "Bearer"}) from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(503, str(exc)) from exc

@app.get("/api/v1/me")
async def get_current_user(authorization: str | None = Header(default=None)):
    return require_user(authorization)

@app.get("/api/v1/me/balances")
async def get_my_paper_balances(authorization: str | None = Header(default=None)):
    user = require_user(authorization)
    return await run_in_threadpool(PAPER_LEDGER.get_balances, user["account_id"])

@app.get("/api/v1/me/ledger")
async def get_my_paper_ledger(
    limit: int = 100,
    authorization: str | None = Header(default=None),
):
    if not 1 <= limit <= 500:
        raise HTTPException(400, "limit must be between 1 and 500.")
    user = require_user(authorization)
    return await run_in_threadpool(PAPER_LEDGER.list_entries, user["account_id"], limit)

@app.get("/api/v1/me/orders")
async def get_my_paper_orders(
    symbol: str | None = None,
    limit: int = 100,
    authorization: str | None = Header(default=None),
):
    if not 1 <= limit <= 500:
        raise HTTPException(400, "limit must be between 1 and 500.")
    user = require_user(authorization)
    normalized = normalize_symbol(symbol) if symbol else None
    return await run_in_threadpool(
        PAPER_ENGINE.list_orders, normalized, limit, user["account_id"]
    )

@app.post("/api/v1/me/orders")
async def create_my_paper_order(
    body: PaperLimitOrderRequest,
    request: Request,
    authorization: str | None = Header(default=None),
):
    user = require_user(authorization)
    try:
        PAPER_ORDER_GUARD.check(
            request.client.host if request.client else "unknown",
            body.quantity, body.price,
        )
        return await run_in_threadpool(
            PAPER_ENGINE.place_limit_order,
            normalize_symbol(body.symbol), body.side, body.quantity,
            body.price, body.client_order_id, user["account_id"],
        )
    except PaperOrderLimitExceeded as exc:
        raise HTTPException(
            429 if "rate limit" in str(exc).lower() else 422,
            str(exc), headers={"Retry-After": str(exc.retry_after)},
        ) from exc
    except KeyError as exc:
        raise HTTPException(404, "Paper account not found.") from exc
    except InsufficientPaperFunds as exc:
        raise HTTPException(409, str(exc)) from exc
    except IdempotencyConflict as exc:
        raise HTTPException(409, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc

@app.delete("/api/v1/me/orders/{order_id}")
async def cancel_my_paper_order(
    order_id: str,
    authorization: str | None = Header(default=None),
):
    user = require_user(authorization)
    try:
        return await run_in_threadpool(
            PAPER_ENGINE.cancel_order, order_id, user["account_id"]
        )
    except KeyError as exc:
        # Avoid disclosing whether another user's order ID exists.
        raise HTTPException(404, "Paper order not found.") from exc
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
