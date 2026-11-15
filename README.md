# ZenCrypto — deployable exchange UI prototype

This is a **frontend-only prototype**, NOT a live exchange. There is no backend, authentication, custody, real-money ledger, actual trading, deposit address, withdrawal mechanism, or privileged admin access. Orders are simulated in browser memory and disappear on refresh. Portfolio balance remains zero. Some market values and chart shapes are illustrative; prices may be refreshed from CoinGecko's public API (rate limits/CORS apply). Never send money to anyone claiming this prototype accepts deposits.

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

The admin page is a visual mockup, not an authenticated admin panel. The chart's timeframe selectors do not load historical candles; integrate an authorized OHLCV feed for real charts. The 24-hour volume display is illustrative, not live.
