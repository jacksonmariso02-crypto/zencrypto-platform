# ZenCrypto — paper-trading exchange prototype

ZenCrypto is a **paper-trading prototype, not a live exchange**. The repository includes a React/Vite trading UI and a separate FastAPI backend with authenticated paper accounts, an append-only double-entry journal, simulated market and limit orders, order history, balance and ledger endpoints, and a limited paper-fund faucet. Supported paper assets are BTC, ETH, SOL, BNB and XRP. Market orders are simulated immediate fills using a client-supplied reference quote; limit orders are matched between ZenCrypto paper accounts by server-side price-time priority, with partial fills and simulated balance reservation. Neither order type is routed to Binance or another external venue. Deposits, withdrawals, custody and real-money trading are disabled. Public market data and historical candles are fetched from Binance public endpoints and remain subject to provider availability; no quote is shown as live until the WebSocket feed passes freshness checks. Never send funds to anyone claiming this prototype accepts deposits.


## Current public application state

The public React interface is a market-data and execution-readiness workspace. It does not expose the legacy paper-order forms, simulated faucet, paper balances, or paper funding flows to visitors. Public market cards, historical candles, and external order-book depth are informational data from public market endpoints. The portfolio page clearly reports that no customer exchange account or custody service is connected.

The exchange connector is deployed in sandbox mode for integration validation. Its public status endpoint always reports customer order routing as disabled. The administrator-only connector order endpoint is restricted to sandbox operation; setting `LIVE_TRADING_ENABLED=true` does not authorize live orders. This is intentional: the current connector uses a platform-level credential and does not provide per-customer exchange authorization or a production custody model.

Do not enable real customer trading by changing an environment variable. A production design requires an approved jurisdiction and legal entity, applicable licensing/registration, KYC/AML and sanctions screening, per-user exchange authorization or a qualified custody partner, encrypted credential/key management, MFA, withdrawal controls, server-side order risk limits, idempotency, reconciliation, immutable audit records, incident response, and independent security review. Real-money execution must remain disabled until those controls have been implemented, tested, and approved.

## Paper trading API additions

- `GET /trade/assets` — authenticated canonical list of supported paper assets.
- `POST /trade/preview` — authenticated, read-only server-side estimate of paper order quantity, fee and available balance; uses a client-supplied reference price and does not execute or reserve funds.
- `POST /trade/limit-orders` — creates a resting paper limit order and reserves simulated quote funds for buys or base assets for sells; requires `Idempotency-Key`. Orders remain open and are not matched yet.
- `GET /trade/limit-orders` — lists the authenticated user's resting paper limit orders.
- `GET /trade/limit-order-book/{symbol}` — returns aggregated ZenCrypto user paper limit bids/asks by price level, without exposing user identities or order IDs. This is separate from external Binance market depth; crossing paper orders are matched by the server-side paper engine.
- `GET /trade/limit-trades` — returns the authenticated user's paper limit-order fills, including side, quantity, execution price, quote amount and fee.

Limit orders use server-side price-time priority matching between different paper users. Crossing orders execute at the resting maker order's limit price. Partial fills are supported; buyer USD and seller base-asset escrow are settled through balanced immutable ledger journals. Remaining reserves are recalculated and excess reserves released. This paper matching engine is separate from Binance external market depth and does not route real orders.
- `POST /trade/limit-orders/{order_id}/cancel` — cancels an open paper limit order and releases its reserved simulated balance through a balanced ledger journal.
- `GET /account/ledger/verify` — authenticated, read-only per-user double-entry integrity check for paper ledger journals.
- `GET /trade/orders` — authenticated, filtered and paginated paper order history; currently only `filled` status is supported because orders execute immediately.
- `POST /trade/orders` — simulated immediate-fill order endpoint; requires an `Idempotency-Key` header and returns `execution_model=IMMEDIATE_FILL_AT_CLIENT_REFERENCE_PRICE` plus `price_source=CLIENT_SUPPLIED_REFERENCE`.

All trading endpoints operate in `PAPER_ONLY` mode. Do not treat simulated fills, balances or summaries as real exchange activity.

## Run locally

Install Node.js 20.19+ or 22.12+ and run:

```bash
npm install
npm run dev
```

Open the URL printed by Vite (usually http://localhost:5173).

## Build

```bash
npm run build
npm run preview
```

The static site output is in `dist/`.

## Publish with Netlify

1. Push this project to a GitHub repository (or upload the generated `dist/` directory to Netlify).
2. In Netlify, select **Add new project → Import an existing project**.
3. Set build command to `npm run build` and publish directory to `dist`.
4. Deploy. Netlify assigns a public `*.netlify.app` URL. You can later add a verified custom domain and configure DNS.

## Publish with Vercel

1. Import the GitHub repository in Vercel.
2. Framework preset: Vite; build command: `npm run build`; output directory: `dist`.
3. Deploy and receive a `*.vercel.app` URL. Connect a domain you own if desired.

## Before enabling real-money operations

- Determine jurisdictions, entity structure, legal counsel, required VASP/MSB/MTL and other licensing or registration, AML/KYC/sanctions obligations, and data protection requirements.
- Implement a secure backend and server-side RBAC with MFA, rate limiting, session management, audit logs, and penetration testing.
- Integrate a licensed exchange/liquidity provider, market data with appropriate rights, reliable matching/execution and reconciliation, and an auditable double-entry ledger.
- Integrate a qualified custody/wallet provider, chain monitoring, address validation, withdrawal approvals, fraud controls and transaction monitoring.
- Establish production infrastructure: encrypted secrets, backups, observability, incident response, security audits, and disaster recovery.
- Do not enable deposits, withdrawals, or live trading before legal and security sign-off.

## Important

The admin page is a visual mockup, not an authenticated admin panel. The chart timeframe selectors load historical OHLCV candles from Binance's public Spot REST API and receive live candle updates through its public WebSocket stream. The homepage ticker displays Binance 24-hour last price, percentage change, quote volume, high and low only when the public ticker feed is fresh. These are informational public-market statistics, not executable ZenCrypto prices.
