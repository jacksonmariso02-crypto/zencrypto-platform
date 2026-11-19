# ZenCrypto deployment checklist

## Frontend

1. Build with Node.js 22+ using `npm ci` (or `npm install`) and `npm run build`.
2. Set `VITE_API_BASE_URL` to the deployed FastAPI backend origin at build time.
3. Configure the exact frontend origin in backend `CORS_ORIGINS`.
4. Deploy the generated `dist/` directory to the chosen static host.
5. Confirm the wallet app is served over HTTPS. Browser wallet providers and clipboard access require a secure context in normal production use.

## Backend

The FastAPI service is in `backend/`.

Suggested Render service settings for an existing backend service:
- Root directory: `backend`
- Build command: `pip install -r requirements.txt`
- Start command: `uvicorn main:app --host 0.0.0.0 --port $PORT`
- Liveness check path: `/health`\n- Readiness check path: `/ready` (returns HTTP 503 until required backend configuration is present)

Set these environment variables in the hosting provider's secret/configuration panel:

| Variable | Value |
|---|---|
| `ZEROX_API_KEY` | Private API key from the 0x developer dashboard; server-side only |
| `CORS_ORIGINS` | Exact frontend origin, comma-separated if there are multiple |
| `ENABLE_PAPER_TRADING` | `false` |
| `APP_SECRET` | Generate a long random secret if legacy auth endpoints are deliberately enabled; never commit it |

Do not add `ZEROX_API_KEY` to a `VITE_*` variable. Frontend variables are bundled into public JavaScript and are not secrets.

## 0x API key setup

1. Create an application in the 0x developer dashboard.
2. Copy the API key from the dashboard.
3. Add it to the backend service as `ZEROX_API_KEY`.
4. Redeploy/restart the backend.
5. Open `/health` and confirm `dex_quote_proxy_configured` is true.
6. Open the app, connect a wallet on Ethereum or Base, and request a WETH/USDC indicative quote.

If the key is missing, the quote API intentionally returns HTTP 503. Do not put the key in GitHub source files, browser local storage, frontend environment variables, screenshots or support messages.

## Deployment verification

- [ ] Backend `/health` returns status `ok`.
- [ ] `dex_quote_proxy_configured` is true.
- [ ] `legacy_paper_api_enabled` is false.
- [ ] The frontend origin is permitted by CORS.
- [ ] Browser wallet connects over HTTPS.
- [ ] Network switching works on supported wallets.
- [ ] ETH/WETH/USDC balances match the wallet and block explorer.
- [ ] A receive address is exactly the connected wallet address.
- [ ] A wallet transfer displays the correct recipient, asset, amount and network before signing.
- [ ] Quote API returns only the curated WETH/USDC pair on Ethereum or Base.
- [ ] Approval spender is checked against the configured AllowanceHolder address.
- [ ] A small testnet transaction has been completed and checked.
- [ ] Mainnet transactions are not promoted as production-ready until legal, security and operational launch gates are signed off.

## Current deployment limitation

The repository's Pages workflow deploys only when code is pushed to `main`. The active development branch is `feature/account-auth-foundation`; changes here are not automatically the public website. Do not merge to `main` solely because CI is green. Review the branch, configure backend secrets, and complete the testnet and launch checks first.
