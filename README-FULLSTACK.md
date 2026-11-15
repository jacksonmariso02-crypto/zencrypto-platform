# ZenCrypto — frontend + sandbox backend

**NOT production ready. Does not execute real trades, custody funds, send withdrawals, perform actual KYC/AML or accept deposits.** This is a runnable integration foundation. The included frontend remains an independent prototype; it is **not yet wired** to the backend API. Backend docs are available at `/docs`.

## Run

Frontend: from project root `npm install && npm run dev` (Node.js 20.19+ or 22.12+).

Backend: `cd backend && python -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt && export APP_SECRET="$(python -c 'import secrets;print(secrets.token_urlsafe(48))')" && uvicorn main:app --reload --host 127.0.0.1 --port 8000`. On Windows use `.venv\\Scripts\\activate` and set APP_SECRET through PowerShell environment variables. API: http://127.0.0.1:8000/docs .

## Implemented backend routes

- POST `/auth/register`, `/auth/login`, `/auth/logout` (password PBKDF2 and expiring revocable sessions; **no MFA or brute-force controls yet**)
- GET `/account/me`, `/account/balances`, `/account/requests`
- POST `/kyc/submit` (collects only a declaration; **does not verify identity**)
- POST `/deposits/request` (request only; no deposit address or balance credit)
- POST `/withdrawals/request` (always blocked, no transfers)
- POST `/trade/orders` (always blocked, no live execution)
- GET `/admin/audit` (server-side admin role check; no admin bootstrap)
- Database schema includes double-entry journal/postings tables but **no settlement posting engine**; audit is mutable SQLite, not tamper-evident.

## Critical launch requirements

1. Engage jurisdiction-specific legal counsel to confirm licensing/registration, KYC, AML, sanctions, travel-rule, consumer protection, data privacy, and transaction monitoring requirements.
2. Replace SQLite with transactional PostgreSQL, implement balanced immutable journal entries, reserved funds, idempotent settlement, reconciliations and controlled migrations.
3. Use a certified KYC/KYB provider, sanctions/PEP screening, risk scoring, ongoing transaction monitoring, suspicious-activity case management and legally required reporting.
4. Integrate licensed exchange execution and qualified custodial wallet/fiat payment providers using signed verified webhooks, idempotency keys, limits and dual authorization for withdrawals.
5. Implement MFA/passkeys, email verification, login throttling, anti-bot, secure cookies/CSRF strategy, admin separation, encryption, secrets vault, SIEM, backups and penetration tests.
6. Independently audit the code and infrastructure and complete provider and regulatory approvals **before enabling real funds**.

## Public deployment

Frontend: Vercel or Netlify; connect a domain you own. Backend: deploy separately to a supported Python hosting platform with HTTPS and a production database **only after security hardening**. Configure frontend `VITE_API_BASE_URL` when wiring it to the backend. No domain is provisioned by this package.

## November 2026 global-compliance extension

Additional endpoints: `GET /compliance/jurisdictions/{ISO2}`, `GET /compliance/status`, `POST /compliance/cases`, `GET /admin/compliance/cases`, `POST /sandbox/faucet` (requires `Idempotency-Key` header, maximum cumulative $1,000 virtual USD), `GET /account/ledger`, `GET /admin/ledger/integrity`.

**Default deny:** No jurisdiction enables live trading. KYC submission is not verification. The faucet only issues paper-money balances and its internal house balance is not a real reserve. No blockchain wallet, fiat payment, matching engine, withdrawal execution, Travel Rule transfer, or KYC vendor is connected. SQLite, bearer tokens and this demo journal are **not** suitable for production financial operations. A real production ledger requires PostgreSQL, stronger append-only controls, serializable transactions, reserved funds, independent reconciliation and externally verifiable audit records. Never deploy these APIs as a public money service.

### Global market go-live gates

1. Obtain counsel's written jurisdiction-by-jurisdiction legal opinions and required authorizations; maintain an allowlist of permitted legal entities, countries, assets, customer types and features.
2. Implement licensed KYC/AML and sanctions providers, ongoing screening, monitoring, Travel Rule, case management, and reporting procedures; avoid storing raw identity documents in the app.
3. Contract authorized custody, banking and liquidity providers, validate signed webhooks and idempotent settlement, implement double authorization and withdrawal limits.
4. Independently audit infrastructure, smart-contract/wallet integrations, security and financial ledger; run penetration tests, incident response exercises and disaster recovery tests.
5. Only then build an independently reviewed production deployment with real-funds feature gates and country-specific launch approvals.

Regulatory reading: https://www.esma.europa.eu/esmas-activities/digital-finance-and-innovation/markets-crypto-assets-regulation-mica ; https://www.fatf-gafi.org/en/topics/virtual-assets.html ; https://www.fincen.gov/resources/statutes-regulations/guidance/application-fincens-regulations-persons-administering
