# ZenCrypto — self-custody wallet and DEX integration

> **Early-stage implementation, not production-ready.** Users control their own wallets and sign all transfers/swaps. The app does not hold customer private keys or maintain custodial balances. DEX quotes require a backend-only 0x API key. Do not use mainnet funds until the integration, legal and security launch gates have been independently reviewed.

## Architecture

- Frontend: React 19 and Vite.
- Wallet: EIP-1193 browser provider.
- Supported networks: Ethereum Mainnet, Base Mainnet, Sepolia and Base Sepolia for wallet reads/transfers; DEX quotes are restricted to Ethereum and Base mainnet.
- Token support: native ETH, WETH and canonical USDC on Ethereum/Base.
- Wallet operations: public balance reads, receive address display, user-signed transfers, WETH wrap/unwrap.
- DEX quotes: server-side 0x Swap API v2 proxy, curated WETH/USDC pair, explicit 100 bps slippage.
- Backend: FastAPI. The old SQLite paper account and sandbox routes are blocked by default and are not used for wallet transactions.
- The blockchain and user's wallet are the source of truth for ownership and transaction signing.

## Run locally

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
export ZEROX_API_KEY="your-private-0x-api-key"
export ENABLE_PAPER_TRADING=false
export CORS_ORIGINS="http://localhost:5173"
uvicorn main:app --reload --host 127.0.0.1 --port 8000
```

On PowerShell, use `$env:ZEROX_API_KEY="..."`, `$env:ENABLE_PAPER_TRADING="false"` and `$env:CORS_ORIGINS="http://localhost:5173"`.

## API routes

| Method | Route | Purpose |
|---|---|---|
| GET | `/health` | API status and DEX key configuration status (never returns the key) |
| GET | `/dex/price` | Indicative WETH/USDC price through 0x Swap API v2 |
| POST | `/dex/quote` | Firm WETH/USDC quote; validates token allowlist, taker and AllowanceHolder spender |

Legacy paper routes remain for isolated tests, but return 404 unless `ENABLE_PAPER_TRADING=true` is deliberately set. Never enable that flag on a public deployment.

## 0x integration

- Create an API key in the 0x developer dashboard.
- Set it as `ZEROX_API_KEY` in the backend host's secret environment settings.
- Do not use a `VITE_*` prefix or put the key in browser code.
- The backend only permits WETH/USDC on Ethereum Mainnet and Base Mainnet.
- The frontend requests an indicative price, then a firm quote.
- If allowance is insufficient, the frontend asks the wallet to approve the exact sell amount to the documented AllowanceHolder address, waits for the approval receipt, fetches a fresh firm quote, then submits the transaction through the wallet.
- The user reviews and signs each approval/swap in their wallet.
- The current implementation does not independently verify 0x response signatures. This must be reviewed before production.

## Wallet behavior

- Connects to an injected EIP-1193 wallet.
- Reads ETH, WETH and USDC balances from public RPC methods.
- Displays the connected wallet address as the user's receive address.
- Sends ETH and ERC-20 WETH/USDC directly from the user's wallet.
- Wraps ETH to WETH and unwraps WETH to ETH using the network's WETH contract.
- Tracks transaction hashes and lets the user check receipts and open the relevant block explorer.
- Keeps a limited local activity list in the current browser only. It is not a complete on-chain transaction history.

## Tests and build

```bash
npm install
npm run build
pip install -r backend/requirements.txt httpx pytest
python -m py_compile backend/main.py
pytest -q
```

CI verifies the build and automated tests. It does not prove that live liquidity exists, a wallet transaction will succeed, the integration is secure, or the product is legally authorized.

## Remaining production gates

- 0x response signature verification or independent quote authenticity review.
- Provider API rate limits and monitoring.
- Token-risk screening and versioned token allowlist.
- Transaction simulation, price impact controls and stale-quote handling.
- Chain indexer with reorg/finality-aware transaction history.
- Wallet compatibility testing and independent security review.
- Written U.S. legal review of the precise interface, assets, fees, governance and target states.
- Incident response, operational monitoring and security audit.

A wallet-to-wallet transfer is not a deposit into a ZenCrypto account. A DEX swap is not a centralized order-book trade. Do not claim regulatory approval or production readiness.
