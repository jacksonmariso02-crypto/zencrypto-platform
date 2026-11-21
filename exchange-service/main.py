import os
import hmac
import re
from decimal import Decimal
from typing import Literal

import ccxt
from fastapi import FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from paper_engine import IdempotencyConflict, PaperMatchingEngine
from paper_ledger import (InsufficientPaperFunds, LedgerIdempotencyConflict, PaperLedger)

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
PAPER_ENGINE = PaperMatchingEngine(PAPER_ENGINE_DB_PATH)
PAPER_LEDGER = PaperLedger(PAPER_ENGINE_DB_PATH)
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


@app.post("/api/v1/paper/orders")
async def create_paper_order(body: PaperLimitOrderRequest, authorization: str | None = Header(default=None)):
    require_admin(authorization)
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
    authorization: str | None = Header(default=None),
):
    require_admin(authorization)
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
