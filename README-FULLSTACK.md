# ZenCrypto — frontend + paper-trading backend

**Not production ready.** ZenCrypto is a paper-trading integration foundation. It does not custody real funds, accept deposits, execute live exchange orders, or process withdrawals. Market data is read from Binance public endpoints; submitted orders are simulated by the ZenCrypto backend at the reference price and are not sent to Binance.

## Current frontend/backend integration

The React frontend is connected to the backend through `VITE_API_BASE_URL` (default: `https://zencrypto-api.onrender.com`). The current UI calls:

- POST `/auth/register`, `/auth/login`, `/auth/logout` — account registration, login and session revocation.
- GET `/account/me`, `/account/balances` — account identity and paper balances.
- POST `/sandbox/faucet` — issues capped virtual USD for testing; it is not a deposit.
- POST `/trade/orders` — immediate paper fill at the supplied reference price, with fee calculation, idempotency protection and journal postings. This is **not** a limit order book or live execution.
- GET `/trade/orders`, `/trade/summary`, `/account/ledger` — personal paper-order history, summary and ledger.

Other backend routes include KYC declaration submission (not identity verification), deposit requests (no address or credit), blocked withdrawal requests, compliance status/cases and role-protected admin endpoints. The frontend currently does not provide a production KYC flow, real deposits/withdrawals, custody, or a live matching engine.

## Run locally

Frontend: from project root run `npm install && npm run dev` (Node.js 20.19+ or 22.12+). Set `VITE_API_BASE_URL` to the backend origin when testing a local backend.

Backend: `cd backend && python -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt && export APP_SECRET="$(python -c 'import secrets;print(secrets.token_urlsafe(48))')" && uvicorn main:app --reload --host 127.0.0.1 --port 8000`. On Windows use `.venv\\Scripts\\activate` and set `APP_SECRET` through PowerShell. API docs: `http://127.0.0.1:8000/docs`.

## Implemented paper-trading behavior

- Passwords are hashed with PBKDF2; bearer sessions expire and can be revoked. MFA, passkeys, email verification and robust brute-force protection are not implemented.
- Paper orders are immediately filled against the submitted reference price. Fees are calculated, balances are checked, idempotency keys prevent duplicate submissions, and balanced journal postings are written in a SQLite transaction.
- The current system does **not** implement limit orders, an order book, price/time-priority matching, partial fills, reserved balances for open orders, or exchange execution.
- The sandbox faucet issues virtual USD only and has a cumulative cap. It does not represent a real reserve or customer deposit.
- KYC submission records a declaration only; it does not verify identity. Deposit requests do not create deposit addresses or credit balances. Withdrawals are blocked.
- Audit records and the SQLite ledger are not independently tamper-evident and are not suitable for production financial operations.

## Deployment

- Frontend: GitHub Pages at `https://jacksonmariso02-crypto.github.io/zencrypto-platform/`.
- Backend: Render at `https://zencrypto-api.onrender.com`.
- The deployment workflow builds the frontend, verifies that the built assets contain the configured API origin, publishes GitHub Pages, then smoke-tests the frontend, backend health/safety flags and browser CORS preflight.
- CI runs the frontend build and backend test suites. A successful Pages deployment and health check do not mean the platform is approved for real-money use.

## Production launch gates

1. Obtain jurisdiction-specific legal advice and required authorizations before offering any regulated service.
2. Replace SQLite with transactional PostgreSQL and implement reviewed migrations, immutable/append-only journal controls, reserved funds, idempotent settlement, reconciliation and externally verifiable audit records.
3. Integrate qualified KYC/KYB, sanctions/PEP screening, ongoing transaction monitoring, case management and legally required reporting.
4. Contract authorized custody, banking, liquidity and execution providers; verify signed webhooks, apply withdrawal limits and dual authorization.
5. Implement MFA/passkeys, email verification, login throttling, secure session/cookie and CSRF strategy, admin separation, encryption, secrets management, SIEM, backups, incident response and penetration testing.
6. Independently audit code, infrastructure and financial controls. Keep real deposits, withdrawals and live trading disabled until all legal, provider and security approvals are complete.

## Regulatory references

- https://www.esma.europa.eu/esmas-activities/digital-finance-and-innovation/markets-crypto-assets-regulation-mica
- https://www.fatf-gafi.org/en/topics/virtual-assets.html
- https://www.fincen.gov/resources/statutes-regulations/guidance/application-fincens-regulations-persons-administering
