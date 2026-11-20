# ZenCrypto project status

Last updated: 2026-11-20

## Current branch

- Working branch: `feature/account-auth-foundation`
- Product direction: user self-custody EVM wallet interface with a curated DEX swap flow.
- Production status: **not production-ready**. Do not advertise the product as audited, licensed, registered or approved.
- Mainnet wallet transfers are user-signed and can move real assets. Use care and small amounts; blockchain transactions are generally irreversible.

## Implemented

- [x] React/Vite frontend and responsive styling.
- [x] EIP-1193 wallet connect and account/network change listeners.
- [x] Network switching for Ethereum, Base, Sepolia and Base Sepolia.
- [x] Native ETH balance reads.
- [x] WETH and USDC balance reads on Ethereum and Base.
- [x] User-controlled receive address and copy action.
- [x] User-signed ETH, WETH and USDC transfers.
- [x] Wallet-signed ETH wrapping and WETH unwrapping.
- [x] Transaction hash, block explorer link and receipt-status lookup.
- [x] Server-side 0x Swap API v2 price and firm quote proxy.
- [x] Curated WETH/USDC pair on Ethereum and Base.
- [x] Exact AllowanceHolder approval flow when the quote provider reports insufficient allowance.
- [x] Wallet-signed swap transaction submission and receipt polling.
- [x] 100 bps (1%) slippage parameter on quote requests.
- [x] Backend token allowlist and spender validation.
- [x] Backend-only `ZEROX_API_KEY` configuration.
- [x] Legacy paper trading, sandbox deposit/withdrawal and simulated account routes blocked by default.
- [x] CI tests for backend route validation and frontend build.
- [x] API response hardening: anti-framing, restrictive frame-ancestors/base-uri CSP, no-sniff, no-referrer, no-store and restricted browser permissions.
- [x] GitHub Actions runs backend safety tests before the frontend production build/deploy.
- [x] Deterministic tests for market-candle normalization, invalid chart parameters, wallet-history input validation, readiness fail-closed behavior and default-disabled legacy paper routes.
- [x] Real Binance Spot OHLCV candlestick history through the Spot REST API, including open/close times, base/quote volume, trade count and closed/forming candle metadata.
- [x] Live Binance kline WebSocket updates merged into the native Lightweight Charts candlestick and volume series.
- [x] Expanded supported Spot chart intervals from 1m through 1M, with up to 500 historical candles loaded by the chart.
- [x] Chart status changes to Live only after a valid live kline message is received; REST history remains a clearly identified snapshot/fallback.
- [x] Cursor-based historical candle pagination using Binance `endTime`, with older pages merged and deduplicated by candle open time.
- [x] Load-older chart control preserves the user's visible time range when older candles are prepended.
- [x] Main candlestick, MACD, KDJ and RSI panels share synchronized visible time ranges, with a reset-view control.
- [x] Paper order history API supports bounded pagination, symbol/side/status filters, total counts and stable ordering.
- [x] Added owner-scoped paper order detail endpoint; requests for another user's order return not found.
- [x] Activity screen loads paper order history in pages and can append older records without changing the paper-only execution boundary.
- [x] Corrected paper sell quantity accounting: gross quote notional determines asset quantity and the fee is deducted from USD proceeds only.

## External configuration required

- Add `ZEROX_API_KEY` to the backend host's secret environment settings. Without it, the DEX quote endpoints intentionally return HTTP 503.
- Set `CORS_ORIGINS` to the exact deployed frontend origin(s).
- Keep `ENABLE_PAPER_TRADING=false` in public deployments.
- Confirm the backend deployment is running the current branch/commit before using the quote UI.

## Remaining engineering and release gates

### DEX integration
- Verify 0x response signatures or add an independent quote authenticity verification layer.
- Add token risk screening and a versioned, independently verified token registry.
- Add explicit price impact, route/source and estimated gas presentation.
- Add quote expiry/staleness handling and stricter transaction simulation.
- Test all approval and swap paths with testnet funds and then controlled mainnet canaries.
- Add durable transaction indexing, reorg handling, confirmations/finality policy and transaction history.
- Add provider rate limiting, abuse controls and monitoring for API-key usage.

### Security
- Complete independent frontend/backend security review.
- Complete wallet transaction threat model and smart-contract/aggregator integration review.
- Add dependency, secret and static analysis scanning.
- Add CSP and other production security headers.
- Add monitoring, alerting, incident response and recovery procedures.
- Conduct wallet compatibility testing across supported browser wallets and hardware wallets.

### U.S. market readiness
- Obtain written legal analysis for the exact interface, protocol integration, token list, fees, governance and target states.
- Determine applicable federal and state requirements before offering access to U.S. users.
- Implement any required geofencing, sanctions, reporting, disclosures and access restrictions based on qualified legal advice.
- Do not publish regulatory approval badges unless the underlying records have been independently verified.

## Production configuration gate\n\nThe backend now exposes `/health` for process liveness and `/ready` for configuration readiness. `/ready` requires a server-side 0x key, explicit CORS origins, `ENABLE_PAPER_TRADING=false`, and a persistent `APP_SECRET`. A passing response means only that these environment checks are satisfied; it does not certify licensing, legal compliance, security audit completion, or successful mainnet trading.\n\nThe DEX quote request schema rejects malformed addresses, non-integer amounts, out-of-range chain IDs and unexpected fields.\n\n## Verification

GitHub Actions has passed the frontend production build and backend test suite on recent commits. CI verifies code compilation/build and the automated tests only; it does not verify live liquidity, wallet signing on mainnet, contract safety, legal compliance or production readiness.

## Legacy paper backend

The old SQLite paper-account and sandbox transfer routes remain in the repository for isolated tests only. They are blocked by default unless `ENABLE_PAPER_TRADING=true`. They are not connected to the self-custody wallet and must not be enabled on a public production deployment.
