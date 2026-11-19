# ZenCrypto project status

Last updated: 2026-11-19

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
- [x] CI tests for backend routes and frontend build.

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

## Verification

GitHub Actions has passed the frontend production build and backend test suite on recent commits. CI verifies code compilation/build and the automated tests only; it does not verify live liquidity, wallet signing on mainnet, contract safety, legal compliance or production readiness.

## Legacy paper backend

The old SQLite paper-account and sandbox transfer routes remain in the repository for isolated tests only. They are blocked by default unless `ENABLE_PAPER_TRADING=true`. They are not connected to the self-custody wallet and must not be enabled on a public production deployment.
