import asyncio
import os
import hmac
import re
import sqlite3
import time
from decimal import Decimal
from typing import Literal

import ccxt
from fastapi import FastAPI, Header, HTTPException, Request, WebSocket, WebSocketDisconnect
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
        "live_order_placement_enabled": LIVE_TRADING_ENABLED,
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
        "sandbox": SANDBOX,
        "live_trading_enabled": LIVE_TRADING_ENABLED,
        "credentials_configured": bool(API_KEY and API_SECRET),
    }

def _readiness_checks():
    database_ok = False
    try:
        with sqlite3.connect(PAPER_ENGINE.db_path, timeout=2) as db:
            result = db.execute("PRAGMA quick_check").fetchone()
            database_ok = bool(result and result[0] == "ok")
    except sqlite3.Error:
        database_ok = False

    auth_secret_ok = (
        len(AUTH_TOKEN_SECRET) >= 32
        and not AUTH_TOKEN_SECRET.startswith("REPLACE_WITH_")
    )
    admin_token_ok = bool(ADMIN_TOKEN)
    return {
        "database": database_ok,
        "user_auth_secret": auth_secret_ok,
        "admin_token": admin_token_ok,
    }


@app.get("/ready")
async def readiness():
    checks = await run_in_threadpool(_readiness_checks)
    ready = all(checks.values())
    payload = {
        "status": "ready" if ready else "not_ready",
        "service": APP_NAME,
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
    if not SANDBOX and not LIVE_TRADING_ENABLED:
        raise HTTPException(403, "Live trading is disabled by server configuration.")
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
