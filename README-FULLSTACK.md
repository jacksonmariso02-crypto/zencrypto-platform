# ZenCrypto — full-stack paper-trading prototype

> **Not production-ready. Paper trading only.** This application does not accept deposits, hold customer assets, execute live exchange orders, or process withdrawals. KYC submission is only a declaration and is not identity verification. No jurisdiction is enabled for live trading.

## Architecture

- Frontend: React 19, Vite, Recharts and Lucide icons.
- API: FastAPI with bearer-token sessions.
- Sandbox persistence: SQLite.
- Ledger: journal and postings tables with per-currency balancing checks and update/delete prevention triggers. This is a demo ledger, not a production financial ledger or an independent audit.
- Market display: public market-data WebSocket for indicative prices. The paper-order API uses the reference price submitted by the client; this is not a production market-data or execution design.
- Real-money functions: deposits and withdrawals are hard-disabled. No wallet, banking, custody, liquidity or exchange execution provider is connected.

## Run locally

### Frontend

From the repository root:

```bash
npm install
VITE_API_BASE_URL=http://127.0.0.1:8000 npm run dev
```

On PowerShell:

```powershell
$env:VITE_API_BASE_URL="http://127.0.0.1:8000"
npm install
npm run dev
```

### Backend

macOS/Linux:

```bash
cd backend
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
export APP_SECRET="$(python -c 'import secrets; print(secrets.token_urlsafe(48))')"
export CORS_ORIGINS="http://localhost:5173"
uvicorn main:app --reload --host 127.0.0.1 --port 8000
```

Windows PowerShell:

```powershell
cd backend
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
$env:APP_SECRET = python -c "import secrets; print(secrets.token_urlsafe(48))"
$env:CORS_ORIGINS = "http://localhost:5173"
uvicorn main:app --reload --host 127.0.0.1 --port 8000
```

API documentation: `http://127.0.0.1:8000/docs`.

Use a unique, randomly generated `APP_SECRET` in each environment. Never commit secrets, database files, access tokens or customer data.

## Implemented API routes

| Method | Route | Purpose / limitation |
|---|---|---|
| GET | `/health` | Health check |
| POST | `/auth/register` | Create a demo user account |
| POST | `/auth/login` | Create a bearer-token session |
| POST | `/auth/logout` | Revoke the current session |
| GET | `/account/me` | Current authenticated account |
| GET | `/account/balances` | Paper balances only |
| GET | `/trade/orders` | Current user's simulated order history |
| POST | `/trade/orders` | Simulated fill only; not exchange execution |
| POST | `/sandbox/faucet` | Issue capped virtual USD using an idempotency key |\n| POST | `/sandbox/deposits` | Credit capped virtual USD and record sandbox deposit history; no blockchain transfer |\n| POST | `/sandbox/withdrawal-requests` | Reserve virtual USD and create a pending sandbox withdrawal request |\n| GET | `/account/requests` | Current user deposit and withdrawal request history |\n| GET | `/admin/requests` | Admin-only sandbox request queue |\n| POST | `/admin/requests/{request_id}/resolve` | Admin-only simulated approval/rejection; no external transfer |
| GET | `/account/ledger` | Current user's demo ledger entries |
| POST | `/deposits/request` | Always blocked |
| POST | `/withdrawals/request` | Always blocked |
| POST | `/kyc/submit` | Records a declaration; does not verify identity |
| GET | `/compliance/status` | Demo account compliance state |
| POST | `/compliance/cases` | Open a demo compliance case |
| GET | `/admin/audit` | Admin role required |
| GET | `/admin/ledger/integrity` | Admin role required; arithmetic check only |

## Tests and build

```bash
# Frontend
npm install
npm run build

# Backend
pip install -r backend/requirements.txt httpx pytest
python -m py_compile backend/main.py
pytest -q
```

GitHub Actions runs the frontend build and backend tests for the working feature branch and pull requests to `main`.

## Wallet connectivity\n\nThe Wallet page can connect to an injected EIP-1193 provider and read the active public account, chain ID and native ETH balance. This is read-only. It does not request signatures, ERC-20 approvals, swaps, or transfers. No DEX router or settlement contract is deployed.\n\n## DEX quote and swap API\n\nThe API exposes `GET /dex/price` and `POST /dex/quote` as a server-side proxy to 0x Swap API v2. Only WETH/USDC on Ethereum Mainnet and Base Mainnet are accepted. The backend reads `ZEROX_API_KEY` from its environment and never returns the key to the browser. The frontend requests indicative pricing, a firm quote, exact token allowance when required, and wallet-signed execution. Configure `ZEROX_API_KEY` in the backend hosting provider before the quote UI can return live prices.\n\n## Sandbox deposit and withdrawal workflow\n\nThe Deposit and Withdraw screens now exercise a paper-only workflow. A sandbox deposit credits virtual USD with a capped, idempotent ledger journal. A sandbox withdrawal reserves virtual USD in a hold account and enters a pending review queue. An administrator can approve the demo request (move the hold to a simulated settlement account) or reject it (release the hold back to the user). Request history is visible to the user. These actions do not generate blockchain addresses, monitor transactions, connect to wallets or banks, or transfer real funds.\n\n## Self-custody DEX gaps — do not represent these as completed\n\n- No deployed or audited swap router, AMM pool or settlement contract.\n- No transaction simulation, slippage protection, ERC-20 allowance flow or wallet-signed swap.\n- No production chain indexer, reorg-safe confirmation service or contract event reconciliation.\n- No production token allowlist, routing/quote service or verified contract-address registry.\n- The current paper balances and sandbox transfer workflows are not connected to the browser wallet.\n\nSee [docs/SELF_CUSTODY_EXCHANGE_ARCHITECTURE.md](docs/SELF_CUSTODY_EXCHANGE_ARCHITECTURE.md) for the selected design and release gates.\n\n## Known gaps — do not represent these as completed

- No MFA/passkeys, email verification, robust login throttling, bot protection or production session/cookie hardening.
- No production PostgreSQL migrations, serializable transaction design, operational backup/restore drills or high-availability setup.
- No production-grade append-only audit log, external reconciliation or independent ledger attestation.
- No live order book, matching engine, exchange connectivity or settlement engine.
- No blockchain wallet, private-key management, custody, deposit monitoring, withdrawal execution or banking/payment integration.
- No verified KYC/KYB, sanctions/PEP screening, transaction monitoring, Travel Rule or regulatory reporting provider.
- No jurisdiction-specific legal opinion, licence/registration verification or approval to serve customers.
- No independent security assessment or penetration test.

## Production-readiness gates

1. Obtain written jurisdiction-by-jurisdiction legal advice and all required authorizations before offering regulated services.
2. Select authorized custody, banking, liquidity and execution providers; complete due diligence and contracts.
3. Design and independently review the production ledger, reserves, settlement, reconciliation and withdrawal approval controls.
4. Integrate verified identity, sanctions, transaction monitoring, case management and legally required reporting.
5. Implement MFA/passkeys, secure session management, abuse prevention, secrets management, encryption, logging, alerting, backups and disaster recovery.
6. Complete independent security review, penetration testing, incident-response exercises and operational readiness review.
7. Keep live-money features deny-by-default and enable them only for specifically approved entities, jurisdictions, products and customer groups.

No live-money feature should be enabled by a UI toggle alone.
