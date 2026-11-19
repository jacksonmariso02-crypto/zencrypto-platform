# ZenCrypto project status

Last updated: 2026-11-19

## Current branch and working scope

- Working branch: `feature/account-auth-foundation`
- Product state: full-stack paper-trading prototype.
- Production state: **not production-ready; no real-money activity permitted by this software**.
- Current frontend and backend are connected for account authentication, balance retrieval, paper-order history, simulated paper orders and the demo faucet.
- A Legal & Compliance disclosure page was added to the frontend. It intentionally labels registration and permissions as unverified rather than displaying invented licences or approvals.

## Implemented

- [x] React/Vite frontend and responsive styling foundation.
- [x] FastAPI backend and SQLite sandbox persistence.
- [x] Registration, login, logout and authenticated account endpoint.
- [x] Paper balances and paper order history.
- [x] Simulated buy/sell flow with fees and ledger postings.
- [x] Idempotent capped virtual USD faucet.
- [x] Demo account ledger endpoint.
- [x] Deposit and withdrawal routes explicitly blocked.
- [x] KYC declaration endpoint clearly marked as non-verification.
- [x] Compliance status and demo case endpoints.
- [x] Admin-role checks for audit and ledger-integrity endpoints.
- [x] Compliance disclosure UI.\n- [x] EIP-1193 injected wallet connection and read-only native ETH balance display.\n- [x] Sandbox deposit flow with capped virtual USD credit and idempotency.\n- [x] Sandbox withdrawal request with virtual-balance reservation.\n- [x] User deposit/withdrawal history and admin-only simulated request resolution.
- [x] GitHub Actions frontend build and backend test workflow.

## Verification

The GitHub Actions workflow named **Paper trading verification** completed successfully for the recent compliance-page commits. It runs:
- `npm install` and `npm run build`
- Python dependency installation
- `python -m py_compile backend/main.py`
- `pytest -q`

A green CI run verifies those automated checks only; it is not a security audit, production approval, regulatory authorization or proof that the application is ready to handle customer funds.

## Remaining engineering work

### Priority 0 — self-custody DEX implementation\n- Complete reviewed smart-contract specification and deployment pipeline.\n- Build a chain indexer with confirmation, reorg and backfill handling.\n- Add verified token registry, route quotes, transaction simulation and wallet-signed swap requests.\n- Add explorer links, transaction lifecycle tracking, gas/slippage/price-impact disclosures.\n- Remove or isolate paper balances and sandbox deposit/withdraw controls from any production-facing build.\n- Complete independent contract audit and written U.S. legal review before mainnet.\n\n### Priority 1 — harden the sandbox and account foundation
- Add MFA/passkeys, email verification, login throttling and abuse controls.
- Review token storage and session lifecycle; move toward secure, HttpOnly cookie sessions with a deliberate CSRF design.
- Add security headers, strict production CORS allowlists and dependency/security scanning.
- Add API contract tests, negative tests, concurrency tests and more complete account-isolation tests.
- Add structured logs, monitoring, alerting and safe error handling.

### Priority 2 — production data and ledger design
- Design PostgreSQL schema and managed migrations.
- Add transaction isolation, row locking, idempotent posting and reservation/available-balance model.
- Add reconciliation jobs, immutable audit evidence and backup/restore tests.
- Keep demo balances and production financial records strictly separated.

### Priority 3 — operational exchange capabilities (only after legal approval)
- Select authorized execution/liquidity, custody and payment providers.
- Define market-data rights, order lifecycle, execution reports, settlement and failure handling.
- Design deposit/withdrawal state machines, risk controls, dual approvals and transaction monitoring.
- Integrate verified KYC/KYB, sanctions screening and required compliance reporting.

### Priority 4 — launch governance
- Obtain jurisdiction-specific legal opinions and required authorizations.
- Complete independent architecture, security and penetration reviews.
- Define incident response, disaster recovery, customer support, disclosures and operational controls.
- Maintain a jurisdiction/product/customer allowlist with default-deny behavior.

## Wallet connection status\n\nThe Wallet page can connect to an injected EIP-1193 browser wallet and read the public address, current chain ID and native ETH balance. This is read-only connectivity. It does not request signatures, token approvals or transactions. It does not constitute a deployed exchange, a deposit address, a withdrawal service or a live swap feature.\n\nSee [Self-Custody Exchange Architecture](SELF_CUSTODY_EXCHANGE_ARCHITECTURE.md) for the selected product model and launch gates.\n\n## Important boundary

This repository is a software prototype. Do not advertise the platform as licensed, regulated, audited, custody-capable or able to process real deposits/withdrawals. Do not collect customer funds or sensitive identity documents through this prototype. No real-money capability should be implemented or enabled until qualified legal, compliance, custody, security and operational reviews are complete.
