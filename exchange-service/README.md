# ZenCrypto Binance Spot Connector (starter)

This is a separate server-side connector. GitHub Pages is static hosting and cannot safely hold exchange secrets or execute private exchange requests.

## Safety defaults
- Binance Spot only; no margin, futures, leverage, deposit or withdrawal endpoints.
- Sandbox is enabled by default.
- Live order placement is disabled by default.
- Private endpoints require a server-side Bearer token.
- Exchange API credentials are read only from backend environment variables. Never commit real keys.
- For any API key, enable only read and spot-trading permissions; disable withdrawals and IP-restrict the key.

## Deploy
1. Deploy this folder as a Python web service (Render or another server host).
2. Build/install command: `pip install -r requirements.txt`
3. Start command: `uvicorn main:app --host 0.0.0.0 --port $PORT`
4. Set the variables from `.env.example` in the host's secret environment settings.
5. Keep `EXCHANGE_SANDBOX=true` and `LIVE_TRADING_ENABLED=false` while testing.
6. Check `/health` and `/docs`.

## API
- `GET /health`
- `GET /api/v1/markets/BTC-USDT/info` (public Spot market precision and trading limits)
- `GET /api/v1/markets/BTC-USDT/ticker`
- `GET /api/v1/markets/BTC-USDT/orderbook?limit=20`
- `GET /api/v1/markets/BTC-USDT/trades?limit=50`
- `GET /api/v1/markets/BTC-USDT/candles?timeframe=1h&limit=100`
- `GET /api/v1/account/balance` (Bearer token required)
- `GET /api/v1/orders` (Bearer token required)
- `POST /api/v1/orders` (Bearer token required; sandbox by default)
- `DELETE /api/v1/orders/{order_id}?symbol=BTC-USDT` (Bearer token required)

## Before real-money trading
This starter is not a production exchange or custody system. The paper matching engine persists its simulated orders and trades in SQLite, but there is not yet a per-user double-entry asset ledger, user authorization, rate limiting, production-grade audit pipeline, withdrawal controls, reconciliation, or operational monitoring. Do not enable live trading until those controls, legal/compliance review, and end-to-end testnet validation are complete. A single shared server API key/token is only suitable for a controlled private prototype, not a public multi-user exchange.
