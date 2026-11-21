# ZenCrypto Binance Spot Connector and Paper Matching Engine

This is a separate server-side service. GitHub Pages is static hosting and cannot safely hold exchange secrets or execute private exchange requests.

## Safety defaults
- Binance Spot connector only; no margin, futures, leverage, deposit or withdrawal endpoints.
- Exchange sandbox is enabled by default.
- Live exchange order placement is disabled by default.
- Paper matching routes require a server-side Bearer token.
- Exchange API credentials are read only from backend environment variables. Never commit real keys.
- For any exchange API key, enable only read and spot-trading permissions; disable withdrawals and IP-restrict the key.

## Deploy
1. Deploy this folder as a separate Python web service (Render or another server host), with this folder as its root directory.
2. Build/install command: `pip install -r requirements.txt`
3. Start command: `uvicorn main:app --host 0.0.0.0 --port $PORT`
4. Set variables from `.env.example` in the host's secret environment settings.
5. Keep `EXCHANGE_SANDBOX=true` and `LIVE_TRADING_ENABLED=false` while testing.
6. Set `PAPER_ENGINE_DB_PATH` to a file on a mounted persistent disk (for example `/var/data/zencrypto-paper-engine.sqlite3`) if paper order history must survive service restarts. The default `/tmp` path is ephemeral.
7. Check `/health` and `/docs`.

## Public market data API
- `GET /health`
- `GET /api/v1/markets/BTC-USDT/info` (Spot market precision and limits)
- `GET /api/v1/markets/BTC-USDT/ticker`
- `GET /api/v1/markets/BTC-USDT/orderbook?limit=20`
- `GET /api/v1/markets/BTC-USDT/trades?limit=50`
- `GET /api/v1/markets/BTC-USDT/candles?timeframe=1h&limit=100`

## Authenticated paper matching API
- `POST /api/v1/paper/orders` (limit orders only; requires Bearer token)
- `GET /api/v1/paper/orders`
- `GET /api/v1/paper/orderbook/BTC-USDT`
- `GET /api/v1/paper/trades`
- `DELETE /api/v1/paper/orders/{order_id}`

The SQLite-backed simulation supports price-time priority, partial fills, resting maker-price execution, cancellation, order-book snapshots, trade history, and idempotent client order IDs. The paper ledger adds isolated simulated accounts, balanced double-entry journal entries, exact Decimal balance calculations, available/locked buckets, idempotent simulated credits, and account-scoped ledger history. Paper credits are administrator-issued test funds only. Account-backed paper orders reserve funds, settle fills, and release cancelled-order reserves through the same SQLite transaction as the matching engine. These components do not call CCXT, process real deposits, or move real assets. The prototype is not a public multi-user exchange.

## Authenticated external exchange API
- `GET /api/v1/account/balance`
- `GET /api/v1/orders`
- `POST /api/v1/orders` (external exchange order; sandbox by default)
- `DELETE /api/v1/orders/{order_id}?symbol=BTC-USDT`

## Before real-money trading
This starter is not a production exchange or custody system. The paper matching engine persists simulated orders and trades in SQLite, but there is not yet end-user authentication/authorization, a distributed rate limiter, production-grade audit pipeline, withdrawal controls, reconciliation, or operational monitoring. Do not enable live trading until those controls, legal/compliance review, and end-to-end testnet validation are complete. A single shared server API key/token is suitable only for a controlled private prototype, not a public multi-user exchange.


## Account-backed paper order settlement

The account-backed paper routes connect the paper matching engine to the double-entry ledger using the same SQLite database transaction. Use these routes for balance-enforced simulations:

- `POST /api/v1/paper/accounts/{account_id}/orders` — reserve quote currency for buys or base currency for sells before matching.
- `GET /api/v1/paper/accounts/{account_id}/orders` — list orders for that paper account.
- `DELETE /api/v1/paper/accounts/{account_id}/orders/{order_id}` — cancel only an order belonging to that account and release its unfilled reserve.

On each fill, the buyer's reserved quote amount is debited, any limit-price difference is returned to available funds, the seller receives quote proceeds, the seller's locked base asset is debited, and the buyer receives base asset. The order update, trade record, and ledger postings commit or roll back together. Account-backed orders match only other account-backed orders; legacy shared paper orders are kept in a separate matching pool.

These endpoints still use the prototype's single administrator Bearer token. They are not end-user authentication, not production custody, and not connected to real deposits, withdrawals, or live exchange execution. The system remains paper-only.


## Paper self-trade prevention

For balance-backed paper orders, the matcher skips resting orders owned by the same account as the incoming order. It continues scanning other eligible counterparties in price/time order. Skipped resting orders and their reserves remain unchanged; an unmatched incoming order remains open with its reserve locked until filled or cancelled. This is account-level self-trade prevention only, not detection of common beneficial ownership across multiple accounts. No production-grade surveillance, identity verification, or anti-manipulation controls are implied.


## Paper-order risk guardrails
- `PAPER_MAX_ORDER_NOTIONAL` caps quantity × limit price (default 100000 paper quote units).
- `PAPER_ORDER_RATE_LIMIT` and `PAPER_ORDER_RATE_WINDOW_SECONDS` configure a per-client-IP sliding-window limit (defaults 30 requests per 60 seconds).
- Oversized paper orders return HTTP 422. Requests over the rate limit return HTTP 429 with a `Retry-After` header.

The current limiter is process-local and uses the direct ASGI client address. It is suitable only as a basic prototype safeguard: it is not shared across multiple workers/instances and must not trust arbitrary forwarded-IP headers. Before public deployment, use a shared Redis/API-gateway limiter behind a correctly configured trusted proxy, and add per-user quotas after real user authentication exists.
