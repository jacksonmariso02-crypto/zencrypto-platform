# ZenCrypto

ZenCrypto is a cryptocurrency-platform **paper-trading prototype** with a React/Vite frontend and a FastAPI sandbox backend.

> **Important: paper trading only.** The project does not accept deposits, custody customer assets, execute real exchange orders, or process withdrawals. No jurisdiction is currently enabled for real-money activity. Do not send funds to this prototype.

## Current capabilities

- React 19 + Vite web interface with English/Chinese labels.
- Account registration, login, logout and authenticated account view.
- FastAPI backend with SQLite sandbox storage.
- Simulated USD test-funds faucet with an idempotency key and a cumulative demo cap.
- Simulated market buy/sell orders using a submitted reference price; these are not routed to an exchange.
- User balances, order history and ledger-entry views.
- Server-side admin-role checks for audit and ledger-integrity endpoints.
- Compliance disclosure page that explicitly distinguishes unverified items from completed controls.\n- Browser wallet connection (EIP-1193), EVM network switching, native ETH/USDC balance reads, receive address display, and user-signed ETH/USDC transfers.\n- 0x Swap API v2 quote proxy for the curated WETH/USDC pair on Ethereum and Base, with wallet-side exact approval and swap signing (requires a backend API key).
- GitHub Actions workflow for frontend build and backend tests.

## Repository layout

- `src.jsx`, `style.css`, `index.html`: frontend.
- `backend/main.py`: FastAPI application and sandbox ledger.
- `backend/requirements.txt`: Python dependencies.
- `tests/test_paper_trading.py`: sandbox trading, idempotency and account-isolation tests.
- `.github/workflows/paper-trading-tests.yml`: CI checks.
- `README-FULLSTACK.md`: detailed setup and API reference.
- `docs/PROJECT_STATUS.md`: implementation status and remaining launch gates.

## Run locally

Requirements: Node.js 22 (or a compatible supported Vite version), Python 3.12+, and Git.

Frontend:
```bash
npm install
npm run dev
```

Backend (macOS/Linux):
```bash
cd backend
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
export APP_SECRET="$(python -c 'import secrets; print(secrets.token_urlsafe(48))')"
uvicorn main:app --reload --host 127.0.0.1 --port 8000
```

Backend API docs: `http://127.0.0.1:8000/docs`.

Set `VITE_API_BASE_URL` to the backend origin when running the frontend against a different backend host. The current frontend has a default hosted API URL; local development should override it with `http://127.0.0.1:8000`.

## Verification

```bash
npm run build
pip install -r backend/requirements.txt httpx pytest
python -m py_compile backend/main.py
pytest -q
```

The GitHub Actions workflow runs the frontend production build, Python compilation and backend tests on pushes to `feature/account-auth-foundation` and pull requests targeting `main`.

## Before any real-money launch

A production exchange requires jurisdiction-specific legal review and required authorizations, verified KYC/AML and sanctions controls, approved custody and payment partners, a production-grade database and ledger, audited security controls, reconciliation, operational monitoring, incident response, independent testing, and explicit country/product launch approval. None of these should be inferred from this prototype. Keep real-money features disabled until all applicable gates have been independently satisfied.

## Project documentation

- [Self-custody DEX architecture for U.S. market review](docs/SELF_CUSTODY_EXCHANGE_ARCHITECTURE.md)\n- [Wallet transfer implementation and release status](docs/WALLET_TRANSFER_IMPLEMENTATION.md)\n- [Architecture and trust boundaries](docs/ARCHITECTURE.md)
- [Current implementation status and roadmap](docs/PROJECT_STATUS.md)
- [Release checklist and production launch gates](docs/RELEASE_CHECKLIST.md)
- [Security policy](SECURITY.md)
- [Full-stack setup and API reference](README-FULLSTACK.md)

See these documents before making deployment or product claims.
