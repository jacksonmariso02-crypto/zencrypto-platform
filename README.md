# ZenCrypto

ZenCrypto is a self-custody EVM wallet interface and early-stage decentralized swap application.

> **Important:** The current build supports connecting a user-controlled wallet, reading public balances, receiving assets to the user's own address, sending ETH/WETH/USDC with wallet signatures, wrapping/unwrapping ETH/WETH, and requesting WETH/USDC quotes through the 0x Swap API. It is not yet a fully audited or production-approved exchange. Do not use funds you cannot afford to lose.

## Implemented

- React 19 + Vite responsive web interface.
- EIP-1193 browser wallet connection.
- Ethereum Mainnet, Base Mainnet, Sepolia and Base Sepolia network switching.
- Native ETH balance reads on supported EVM networks.
- WETH and USDC balance reads on Ethereum and Base.
- User-controlled receive address and copy action.
- Wallet-signed ETH, WETH and USDC transfers.
- Wallet-signed ETH-to-WETH wrapping and WETH-to-ETH unwrapping.
- Server-side 0x Swap API v2 proxy for the curated WETH/USDC pair on Ethereum and Base.
- Firm-quote flow with exact AllowanceHolder approval when required, wallet-signed swap transaction and receipt lookup.
- Legacy SQLite paper-account API routes are disabled by default.

## Important setup

The DEX quote service requires a 0x API key. Configure `ZEROX_API_KEY` as a secret environment variable on the backend host. Never put it in a `VITE_*` frontend variable or commit it to Git.

Keep `ENABLE_PAPER_TRADING=false` in public deployments. The old paper-account and sandbox routes are blocked by default and are retained only for isolated legacy tests.

## Run locally

Requirements: Node.js 22+, Python 3.12+, and Git.

Frontend:

```bash
npm install
VITE_API_BASE_URL=http://127.0.0.1:8000 npm run dev
```

Backend:

```bash
cd backend
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
export ZEROX_API_KEY="your-server-side-0x-key"
export ENABLE_PAPER_TRADING=false
export CORS_ORIGINS="http://localhost:5173"
uvicorn main:app --reload --host 127.0.0.1 --port 8000
```

On Windows PowerShell, use `$env:ZEROX_API_KEY="..."`, `$env:ENABLE_PAPER_TRADING="false"`, and `$env:CORS_ORIGINS="http://localhost:5173"`.

API health: `http://127.0.0.1:8000/health`.
API docs: `http://127.0.0.1:8000/docs`.

Set a unique `APP_SECRET` for any environment where legacy account endpoints are intentionally enabled. Do not commit secrets, private keys, seed phrases, database files or customer information.

## Verification

```bash
npm install
npm run build
pip install -r backend/requirements.txt httpx pytest
python -m py_compile backend/main.py
pytest -q
```

GitHub Actions builds the frontend and runs Python compilation and backend tests.

## Not yet production-ready

- Live 0x quotes and swaps have not been end-to-end tested with mainnet funds.
- The 0x API response signature is not yet independently verified.
- No production chain indexer, reorg-aware transaction history or confirmation service.
- No audited contract integration/security review or bug bounty.
- No complete token-risk screening, price-impact limits, advanced transaction simulation or wallet compatibility matrix.
- No written U.S. legal determination for the final product, token list, fee model, governance or user jurisdictions.
- No claim is made that ZenCrypto is licensed, registered, audited or approved.

A wallet-to-wallet transfer is not a deposit into a ZenCrypto account. A DEX swap is not an internal exchange order-book fill. Users control their keys and approve transactions in their wallet. Mainnet functionality must remain restricted until technical and legal launch gates are satisfied.

## Project documentation

- [Self-custody DEX architecture](docs/SELF_CUSTODY_EXCHANGE_ARCHITECTURE.md)
- [Wallet transfer implementation](docs/WALLET_TRANSFER_IMPLEMENTATION.md)
- [Deployment checklist](docs/DEPLOYMENT.md)
- [Architecture and trust boundaries](docs/ARCHITECTURE.md)
- [Project status and roadmap](docs/PROJECT_STATUS.md)
- [Release checklist](docs/RELEASE_CHECKLIST.md)
- [Security policy](SECURITY.md)
- [Full-stack setup and API reference](README-FULLSTACK.md)
