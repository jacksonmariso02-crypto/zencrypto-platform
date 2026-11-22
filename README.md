# ZenCrypto — customer-owned exchange account connector

ZenCrypto is moving to a **customer-owned exchange account model**. Customers keep their assets in their own exchange accounts and authorize ZenCrypto to read account data and, after a separate production approval, submit spot orders. ZenCrypto does not custody customer assets or request withdrawal permission. Public market data is currently sourced from Binance. Customer order routing remains disabled; this repository currently implements account authorization verification, encrypted credential storage, and read-only balance retrieval only.


## Current public application state

The public React interface is a market-data and execution-readiness workspace. It does not expose the legacy paper-order forms, simulated faucet, paper balances, or paper funding flows to visitors. Public market cards, historical candles, and external order-book depth are informational data from public market endpoints. The portfolio page clearly reports that no customer exchange account or custody service is connected.

The legacy platform-level exchange connector remains sandbox-locked. Customer API-key verification and balance retrieval call Binance's live read-only account endpoints directly with the individual customer's encrypted credentials. The public status endpoint always reports customer order routing as disabled. The administrator-only connector order endpoint is restricted to sandbox operation; setting `LIVE_TRADING_ENABLED=true` does not authorize live orders. This is intentional: the current connector uses a platform-level credential and does not provide per-customer exchange authorization or a production custody model.

Do not enable real customer trading by changing an environment variable. A production design requires an approved jurisdiction and legal entity, applicable licensing/registration, KYC/AML and sanctions screening, per-user exchange authorization or a qualified custody partner, encrypted credential/key management, MFA, withdrawal controls, server-side order risk limits, idempotency, reconciliation, immutable audit records, incident response, and independent security review. Real-money execution must remain disabled until those controls have been implemented, tested, and approved.


## Customer-owned Binance account connection (A model)

The public account page supports a customer-owned Binance connection:

- `GET /api/v1/user/exchange-connections` — returns the signed-in customer's connection status and masked API-key suffix.
- `POST /api/v1/user/exchange-connections/binance` — verifies a customer's Binance API key using the Binance account API-restrictions endpoint and a read-only balance request. It requires account reading and Spot & Margin Trading permission, and rejects any key with withdrawals enabled.
- `GET /api/v1/user/exchange-connections/binance/balances` — reads non-zero balances from the connected customer's Binance Spot account.
- `DELETE /api/v1/user/exchange-connections/binance` — deletes the stored encrypted API authorization.

The exchange connector validates the platform session by calling the platform API's authenticated `/account/me` endpoint. Connection records are scoped to the platform user ID. API key and secret are encrypted together using AES-256-GCM with a per-record random nonce and user-specific associated data. The raw credentials are never returned by the API; only a masked key suffix is returned. The encryption key is generated and stored by Render as `EXCHANGE_CREDENTIAL_ENCRYPTION_KEY`. Do not rotate or replace this secret without first performing a controlled credential re-encryption migration, or existing connected keys will become undecryptable.

Create a dedicated Binance API key. Enable reading and the combined Spot & Margin Trading permission only if Binance requires it for Spot orders; ZenCrypto currently uses Spot endpoints only. Keep withdrawals, Futures and any unnecessary permissions disabled. Enable IP restrictions and whitelist the service's confirmed outbound IP address/range when the deployment network is finalized. Never submit a Binance password, 2FA code, seed phrase or withdrawal-enabled API key.

**Important:** this increment does not place customer orders. Live order routing remains hard-disabled until customer-specific order validation, risk limits, idempotency, order reconciliation, audit trails, key-rotation procedures, incident response, security review and applicable legal/compliance approvals are complete. The legacy public paper-trading endpoints in the platform API return HTTP 410 by default; they can only be re-enabled explicitly for isolated development/test environments with `PAPER_TRADING_ENABLED=true`.

## Legacy simulated trading internals

The former paper matching engine and ledger remain in the repository for historical development and isolated regression tests. Their public platform API routes are disabled by default and return HTTP 410. They are not part of the current customer-facing product. The `PAPER_TRADING_ENABLED=true` flag exists only for isolated development/test environments and must remain unset/false in production.

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
