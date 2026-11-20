# ZenCrypto project status

Last updated: 2026-11-20

## Current branch and product boundary

- Working branch: `feature/account-auth-foundation`
- Product: non-custodial EVM wallet interface with live market charts and a curated DEX integration.
- Production status: **not production-ready**. Do not represent ZenCrypto as audited, licensed, registered, or approved.
- Users retain their private keys and sign wallet transactions in their own wallet. ZenCrypto does not custody customer keys or balances.
- Mainnet wallet transfers can move real assets and are generally irreversible. The DEX swap feature remains disabled by default.

## Implemented

### Wallet and on-chain activity
- [x] EIP-1193 wallet connection and account/network change listeners.
- [x] Ethereum, Base, Sepolia, and Base Sepolia network switching.
- [x] Native ETH balance reads.
- [x] WETH and USDC balance reads on Ethereum and Base.
- [x] User-controlled receive address and copy action.
- [x] User-signed ETH, WETH, and USDC transfers.
- [x] Wallet-signed ETH wrapping and WETH unwrapping.
- [x] Transaction hash, block explorer link, and receipt-status lookup.
- [x] Public wallet activity lookup for Ethereum and Base through Routescan.

### Live market charts
- [x] Binance Spot OHLCV history with open/close times, base/quote volume, trade count, and forming/closed candle metadata.
- [x] Ten chart symbols: BTC, ETH, SOL, BNB, XRP, ADA, DOGE, LTC, LINK, and DOT against USDT.
- [x] Live Binance kline WebSocket updates merged into Lightweight Charts candlestick and volume series.
- [x] Supported Spot intervals from 1m through 1M.
- [x] REST history fallback when the live stream is unavailable; UI distinguishes live updates from snapshots/fallback.
- [x] Historical candle pagination using Binance endTime, with older pages merged and deduplicated by candle open time.
- [x] Load-older control preserves the visible time range.
- [x] Synchronized candlestick, MACD, KDJ, and RSI time ranges.
- [x] Moving averages, Bollinger Bands, volume, and reset-view controls.
- [x] Local watchlist and browser price alerts.
- [x] Selected-pair live 24-hour ticker statistics: last price, absolute/percentage change, high/low, base/quote volume, weighted average, and trade count.
- [x] Portfolio market-feed freshness expires after 30 seconds even if a WebSocket silently stalls; the UI reevaluates freshness every five seconds.
- [x] Candlestick, order-book depth, recent-trades, and selected-pair 24-hour ticker indicators independently expire to `Stale` when their own feed stops updating, rather than inheriting an old `Live` state.

### DEX integration (not released)
- [x] Server-side 0x Swap API v2 price and firm-quote proxy.
- [x] Curated WETH/USDC pair on Ethereum and Base.
- [x] AllowanceHolder approval flow when the quote provider reports insufficient allowance.
- [x] Wallet-signed swap submission and receipt polling in the frontend code.
- [x] Backend token allowlist and spender validation.
- [x] Backend-only `ZEROX_API_KEY` configuration.
- [x] Independent backend and frontend release flags for mainnet swaps; both default to disabled.

### Retired simulation
- [x] Legacy simulated-account, paper-order, sandbox funding, deposit, withdrawal, KYC mock, and compliance mock endpoints return HTTP 410 Gone.
- [x] The legacy `ENABLE_PAPER_TRADING` flag cannot reactivate retired endpoints.
- [x] The frontend no longer presents simulated balances or simulated order history as real customer funds or activity.
- Historical simulation source/data must not be treated as customer assets or merged into wallet balances.

### Platform controls
- [x] React/Vite responsive frontend.
- [x] API security headers including anti-framing, restrictive CSP directives, no-sniff, no-referrer, no-store, and restricted browser permissions.
- [x] `/health` liveness endpoint and `/ready` configuration-readiness endpoint.
- [x] CI compiles/tests the backend and builds the frontend before deployment.

## Required external configuration

- Set `ZEROX_API_KEY` only in the backend host's secret environment settings. Never put it in frontend build variables.
- Set `CORS_ORIGINS` to exact deployed frontend origin(s).
- Set a high-entropy persistent `APP_SECRET`.
- Keep `ENABLE_MAINNET_SWAPS=false` on the backend and `VITE_ENABLE_MAINNET_SWAPS=false` (or unset) in frontend builds until all release gates are approved.
- Confirm the backend host is running the intended branch and commit before relying on API features.

## Remaining engineering and release gates

### DEX safety and execution
- Add independent quote authenticity checks and a versioned, independently verified token registry.
- Add token risk screening, price impact, route/source, and estimated gas presentation.
- Enforce quote expiry and stale-quote rejection; add stricter transaction simulation.
- Test approval and swap flows on testnets before controlled mainnet canaries.
- Add durable transaction indexing, reorg handling, confirmation/finality policy, and user-visible swap history.
- Add provider rate limits, abuse controls, and API-key usage monitoring.

### Security and operations
- Complete independent frontend/backend security review and wallet transaction threat modelling.
- Run dependency, secret, and static-analysis scans.
- Add production monitoring, alerting, incident response, and recovery procedures.
- Test supported browser wallets and hardware wallets.
- Verify backup, restore, database, and deployment rollback procedures where applicable.

### Legal and market access
- Obtain written legal analysis for the exact interface, protocol integration, token list, fees, governance, and target jurisdictions.
- Determine applicable federal and state requirements before offering access to U.S. users.
- Implement any required geofencing, sanctions screening, reporting, disclosures, and access restrictions based on qualified legal advice.
- Do not publish regulatory approval or audit badges without independent verification.

## Readiness semantics

A passing `/ready` response means only that configured service dependencies and environment checks pass. It does not certify legal compliance, licensing, independent security audit completion, liquidity, or successful mainnet execution.

The chart endpoint serves public market data only. OHLCV candles are informational and are not executable DEX quotes or guaranteed fill prices.

## Latest verification

GitHub Actions should pass:
- Python syntax compilation.
- Backend API and market-data tests.
- Frontend production build.

CI results verify the tested code paths only; they do not certify the live deployment, external provider availability, wallet signing on mainnet, contract safety, or production readiness.
