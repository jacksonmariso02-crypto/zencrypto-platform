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
This starter is not a production exchange or custody system. The paper matching engine persists simulated orders and trades in SQLite, but the new user-authentication layer is still an early prototype: it does not yet provide email verification, password reset, MFA, refresh-token rotation/revocation, distributed rate limiting, production-grade audit pipeline, withdrawal controls, reconciliation, or operational monitoring. Do not enable live trading until those controls, legal/compliance review, and end-to-end testnet validation are complete. A single shared server API key/token is suitable only for a controlled private prototype, not a public multi-user exchange.


## Account-backed paper order settlement

The account-backed paper routes connect the paper matching engine to the double-entry ledger using the same SQLite database transaction. Use these routes for balance-enforced simulations:

- `POST /api/v1/paper/accounts/{account_id}/orders` — reserve quote currency for buys or base currency for sells before matching.
- `GET /api/v1/paper/accounts/{account_id}/orders` — list orders for that paper account.
- `DELETE /api/v1/paper/accounts/{account_id}/orders/{order_id}` — cancel only an order belonging to that account and release its unfilled reserve.

On each fill, the buyer's reserved quote amount is debited, any limit-price difference is returned to available funds, the seller receives quote proceeds, the seller's locked base asset is debited, and the buyer receives base asset. The order update, trade record, and ledger postings commit or roll back together. Account-backed orders match only other account-backed orders; legacy shared paper orders are kept in a separate matching pool.

The account-scoped administrative routes above still require the administrator Bearer token. The new `/api/v1/me/*` routes use the authenticated user's account identity from the signed token and do not accept a caller-supplied account ID. All balances remain simulated; this is not production custody and is not connected to real deposits, withdrawals, or live exchange execution.


## Paper self-trade prevention

For balance-backed paper orders, the matcher skips resting orders owned by the same account as the incoming order. It continues scanning other eligible counterparties in price/time order. Skipped resting orders and their reserves remain unchanged; an unmatched incoming order remains open with its reserve locked until filled or cancelled. This is account-level self-trade prevention only, not detection of common beneficial ownership across multiple accounts. No production-grade surveillance, identity verification, or anti-manipulation controls are implied.


## Paper-order risk guardrails
- `PAPER_MAX_ORDER_NOTIONAL` caps quantity × limit price (default 100000 paper quote units).
- `PAPER_ORDER_RATE_LIMIT` and `PAPER_ORDER_RATE_WINDOW_SECONDS` configure a per-client-IP sliding-window limit (defaults 30 requests per 60 seconds).
- Oversized paper orders return HTTP 422. Requests over the rate limit return HTTP 429 with a `Retry-After` header.

The current limiter is process-local and uses the direct ASGI client address. It is suitable only as a basic prototype safeguard: it is not shared across multiple workers/instances and must not trust arbitrary forwarded-IP headers. Before public deployment, use a shared Redis/API-gateway limiter behind a correctly configured trusted proxy, and add per-user quotas after real user authentication exists.


## End-user paper authentication (prototype)
- `POST /api/v1/auth/register` — create a username and paper account; returns a short-lived bearer access token.
- `POST /api/v1/auth/login` — authenticate and receive a new access token.
- `GET /api/v1/me` — current authenticated user.
- `GET /api/v1/me/balances` — only the caller's paper balances.
- `GET /api/v1/me/ledger?limit=100` — only the caller's paper ledger entries.
- `GET /api/v1/me/orders?symbol=BTC-USDT` — only the caller's paper orders.
- `POST /api/v1/me/orders` — place a balance-backed order in the caller's account.
- `DELETE /api/v1/me/orders/{order_id}` — cancel only an order owned by the caller.

Set `AUTH_TOKEN_SECRET` to a cryptographically random secret of at least 32 characters. Access tokens are HS256-signed and expire after `AUTH_TOKEN_TTL_SECONDS` (default 900 seconds). Passwords are stored as salted scrypt hashes. User registration and paper-account creation share one SQLite transaction. Administrator-only credit endpoints remain separate and are intended only to seed test funds.

This is not yet public-ready authentication: there is no email verification, MFA, password reset, token revocation/refresh, account recovery, or distributed authentication throttling. Keep registration behind a controlled test environment until those features and a security review are complete. Never reuse the admin token as the JWT signing secret.


## Paper maker/taker fees
- Configure `PAPER_MAKER_FEE_BPS` and `PAPER_TAKER_FEE_BPS` (basis points; 100 bps = 1%). Defaults are 10 bps (0.10%) maker and 20 bps (0.20%) taker. Set either to 0 to disable that rate. The prototype bounds each rate to 0-1000 bps.
- Fees are charged only on account-backed paper trades. Legacy shared paper orders do not create account ledger fees.
- The buyer fee is deducted from the base asset received; the seller fee is deducted from quote proceeds. Both are credited to the simulated `platform:fees` ledger account in the same database transaction as the fill.
- Each paper trade records maker/taker fee amounts and fee assets. `GET /api/v1/paper/fees` returns aggregate simulated fee balances and requires the administrator token.
- Fee postings roll back with the trade if any part of settlement fails. No real exchange fees or actual revenue are generated.

Fee rounding to exchange-specific asset precision, fee tiers, rebates, discounts, tax/accounting treatment, and a production finance reconciliation workflow are not implemented. These are paper simulation defaults, not a published customer fee schedule.


## Market rules and order precision (paper trading)
- `GET /api/v1/markets` lists enabled paper markets and their base/quote assets, tick size, quantity step, minimum quantity, minimum notional, and display precision. It is public market metadata.
- `GET /api/v1/markets/{symbol}` returns one enabled market. Both `BTC/USDT` and normalized `BTC-USDT` forms are accepted.
- Order validation is enforced in the matching engine before idempotency handling, balance reservation, or matching. Price must be an exact multiple of the market tick size; quantity must be an exact multiple of its step size, meet minimum quantity, and satisfy minimum notional.
- The initial explicit paper-market registry contains BTC/USDT, ETH/USDT, SOL/USDT, BNB/USDT, XRP/USDT, and DOGE/USDT. Other symbols are rejected until a market rule is deliberately configured.
- Current simulated defaults are maintained in `market_rules.py`. They are development defaults, not a live exchange's current filters. Before any real exchange integration, synchronize rules from the venue's authoritative exchange-info endpoint, persist versioned snapshots, and define asset-specific rounding and precision policies.
- Display precision is metadata only; order validity uses exact Decimal multiples, not floating-point rounding. The engine rejects invalid input rather than silently rounding user orders.


## Paper-market OHLCV candles
- `GET /api/v1/paper/markets/{symbol}/candles?timeframe=1m&limit=100&since=...` returns candles derived exclusively from persisted fills in ZenCrypto's paper matching engine. It does not query or mix external exchange prices.
- Supported intervals: `1m`, `5m`, `15m`, `30m`, `1h`, `4h`, and `1d`. `limit` is 1-1000. Optional `since` is a UTC Unix timestamp in milliseconds.
- Candle timestamps are UTC bucket-start milliseconds. OHLC values follow paper trade chronology; volume is the exact Decimal sum of executed base-asset quantities.
- The endpoint returns only buckets with at least one paper fill. It does not fabricate empty candles or seed history. Markets without paper fills return an empty array.
- A SQLite index on `(symbol, created_at, id)` supports time-bucket selection. Candle values are aggregated in Python Decimal arithmetic to avoid binary floating-point volume errors.
- Existing `/api/v1/markets/{symbol}/candles` remains the external CCXT venue's OHLCV endpoint. Use that for reference-market candles and the new `/api/v1/paper/markets/...` endpoint for ZenCrypto's own simulated market history.


## Realtime paper-market trade stream
- WebSocket: `/ws/v1/paper/trades?symbol=BTC-USDT`. Omit `symbol` to subscribe to all public paper-market fills.
- The first frame is a `ready` event containing the normalized symbol and current cursor. Subsequent frames use `type: trade` and contain one persisted paper fill.
- Reconnect with `since_id=<last received trade id>` to replay fills created after the last successfully processed event. Trade IDs are monotonically increasing database IDs; clients should persist the last processed ID to avoid gaps after reconnects.
- When `since_id` is omitted, the stream starts at the latest trade ID for that symbol, so it delivers new fills rather than replaying all historical data. Use the REST trade-history endpoint for backfill.
- The stream polls the durable SQLite trade table every 500ms and returns batches of at most 500 events. This makes reconnect/resume work across service processes sharing the same database, unlike an in-memory-only broadcast manager.
- Public stream payloads expose trade price, quantity, timestamp and opaque order IDs only; account IDs and balances are never included. This is a paper-trading feed, not a live exchange feed.


## Shared external market-data WebSocket
- Connect to `/ws/v1/markets/BTC-USDT` for external Binance Spot reference ticker and top-20 order-book snapshots.
- The first message is a `ready` event identifying the external source, venue, normalized symbol, sandbox flag, and polling interval. Subsequent `market_snapshot` messages contain a sanitized ticker and order book. Temporary provider failures emit a generic `market_error` message without credentials or internal exception details.
- `GET /api/v1/market-data/status` reports the configured provider, market type, transport, polling interval and sandbox setting.
- The service shares one polling task per symbol across connected clients, rather than issuing a separate exchange request for each WebSocket subscriber. Slow clients have bounded queues; stale snapshots are dropped in favor of the newest market state.
- Polling defaults to 2 seconds and is clamped to a minimum of 2 seconds through `MARKET_STREAM_INTERVAL_SECONDS`. This is a REST-polling WebSocket fan-out, not a native Binance WebSocket feed. It is suitable for development and moderate test traffic, not high-frequency or production-scale market data.
- Each poll fetches a ticker and top-20 order book from the configured external venue. External reference prices and depth are informational and are never used as paper fills, user balances, or P&L.
- For production scale, replace the polling worker with a venue-native streaming adapter, central message broker, connection quotas, provider sequence/gap recovery, and monitored backpressure.


## Order-book depth and market metrics
- `GET /api/v1/markets/{symbol}/depth?limit=20` returns normalized depth from the external Binance Spot order book. Allowed limits: 5, 10, 20, 50, 100.
- `GET /api/v1/paper/orderbook/{symbol}/depth?limit=50` returns ZenCrypto paper resting orders and requires the administrator Bearer token. Allowed limits: 1-100.
- Both endpoints return aggregated price levels with per-level quantity, cumulative base quantity, quote notional, and cumulative quote notional. Metrics include best bid/ask, spread, mid-price, spread in basis points, bid/ask depth quantity and notional, and normalized bid/ask imbalance.
- Paper depth is derived only from open and partially filled paper orders with positive remaining quantity. Multiple orders at the same price are aggregated before applying the requested level limit.
- External depth is sourced only from the configured external spot venue. The external market WebSocket now includes the normalized `depth` object alongside the raw top-20 book snapshot.
- Imbalance is `(bid depth quantity - ask depth quantity) / total depth quantity` over the returned levels. It is a descriptive book metric, not a predictive signal or execution guarantee. Depth snapshots are not an atomic cross-venue view and can change immediately after retrieval.
- The existing paper order-book endpoint remains backward compatible. The new `/depth` endpoints provide the enriched schema.


## Incremental ZenCrypto paper order-book WebSocket
- Connect to `/ws/v1/paper/orderbook/BTC-USDT` for the platform's own simulated resting-order depth.
- The first event is `ready`, followed by a complete `depth_snapshot` with sequence 1.
- Later events are `depth_delta` messages. Each side contains changed `[price, quantity]` pairs; a quantity of `"0"` removes that price level. Metrics are refreshed with every delta.
- Sequence numbers are per connection. `previous_sequence` identifies the immediately preceding event. If a client detects a gap or reconnects, it must fetch `/api/v1/paper/orderbook/{symbol}/depth` to rebuild a fresh snapshot before applying more deltas.
- One shared poller per symbol reads the durable paper matching-engine database. Unchanged snapshots are suppressed; bounded subscriber queues discard stale snapshots for slow clients so the next delivered event represents the latest book state.
- Polling defaults to 500ms and is clamped to a minimum of 250ms by `PAPER_BOOK_STREAM_INTERVAL_SECONDS`. This is shared database polling, not event-driven matching-engine publication; it is an intermediate implementation for development and moderate traffic. Production should publish order mutations from the matching engine through a durable broker/outbox and use explicit snapshot sequence/version recovery.
- The stream contains ZenCrypto paper orders only. It never combines external venue depth or exposes account IDs and balances.


## Deployment readiness probe
- Render should use `/ready` as the exchange service health-check path; `/health` remains a lightweight liveness response.
- `/ready` verifies the mounted SQLite database with `PRAGMA quick_check`, confirms a non-placeholder user-token signing secret is configured, and confirms the administrator token exists.
- The response reports boolean checks only and never returns secret values or the database path. It returns HTTP 503 until all required checks pass.
- The Render Blueprint must declare `AUTH_TOKEN_SECRET` exactly once with `generateValue: true`; keep this generated value stable across deploys so existing signed user tokens remain verifiable.


### Sandbox-only readiness gates
- The readiness probe also verifies that the required paper matching, user, account, and ledger tables exist in the SQLite database.
- The exchange service is considered ready only when Binance sandbox mode is enabled and live trading is explicitly disabled.
- The administrator token must be at least 32 characters, as must the user-token signing secret. The readiness response exposes only pass/fail booleans.
- A deployment with sandbox disabled, live trading enabled, missing schema, or missing/short secrets fails readiness with HTTP 503. Do not weaken these checks to force a deployment green.
