# ZenCrypto self-custody exchange architecture

**Target model:** non-custodial, wallet-connected decentralized exchange (DEX) for a U.S.-focused product review. This document is a technical target architecture, not a legal opinion or evidence of regulatory approval.

## 1. Product boundary

The selected model is user self-custody:
- Users create or import wallets in software they control (for example, a compatible browser wallet).
- ZenCrypto must never request, receive, store, back up, or transmit a user's seed phrase or private key.
- Users approve token allowances and sign each transaction in their own wallet.
- Smart contracts execute swaps and settle assets on-chain.
- ZenCrypto's web app reads public chain data and prepares unsigned transaction requests; the wallet displays the transaction and asks the user to approve it.
- ZenCrypto does not maintain a platform-controlled customer asset balance, pooled customer wallet, withdrawal queue, or internal customer-to-customer transfer ledger.

A self-custody wallet connection does **not** by itself make a centralized order book or exchange non-custodial. The product must not take possession of assets or gain unilateral control over them through upgrade keys, admin functions, arbitrary transfer permissions, or privileged settlement.

## 2. Recommended first production architecture

Start with a narrowly scoped spot DEX on one EVM network, rather than attempting a multi-chain CEX:
- Frontend: React + Vite, wallet provider discovery, chain validation, token metadata, quote preview, transaction status.
- Wallet interface: EIP-1193 injected provider first; add WalletConnect only after provider and security review.
- Chain data: self-operated or contracted RPC endpoints with independent fallback providers; WebSocket subscriptions for new blocks and logs.
- Quote/routing service: read-only route discovery and quote calculation. Quotes are indicative and must be simulated against the target chain immediately before wallet submission.
- Execution: reviewed and independently audited swap contracts/router deployed at verified addresses. Users sign ERC-20 approvals and swaps in their own wallets.
- Indexer: listens to canonical chain logs, handles confirmations, reorgs, duplicate events and backfills; stores derived public transaction state only.
- API: market metadata, token allowlist, route/quote data, transaction lookup, public analytics and operational health. It must not fabricate balances or mark a transaction complete before chain confirmation.
- Database: PostgreSQL for market metadata, token lists, quotes, transaction tracking, audit events and operational records. It is not the source of truth for token ownership; the blockchain is.
- Observability: RPC health, indexer lag, reorg alerts, transaction failure rates, contract pause state and provider disagreement.

## 3. Swap lifecycle

1. Connect wallet and read public address and chain ID.
2. Verify the chain is explicitly supported; otherwise block swap preparation and explain the network mismatch.
3. Load token metadata from a curated token registry. Do not trust arbitrary token symbols or logos.
4. Calculate a quote from the reviewed router/pool state and show price impact, slippage bound, route, gas estimate, fee and minimum received.
5. Simulate the exact transaction against the current chain state. Reject stale, reverted, unsupported or suspicious routes.
6. If required, ask the wallet to approve a limited ERC-20 allowance. Prefer exact or bounded approvals; explain that approval is separate from a swap.
7. Ask the wallet to sign and broadcast the swap transaction. The private key never leaves the wallet.
8. Track the transaction hash, receipt, status and confirmations. Handle pending, replaced, dropped, reverted and reorged transactions.
9. Update the UI from confirmed on-chain events. Never credit an off-chain account balance as a substitute for settlement.
10. Provide a block explorer link and downloadable transaction details.

## 4. What “deposit” and “withdrawal” mean in self-custody

For a wallet-connected DEX, there is no ordinary deposit into a ZenCrypto-controlled account and no platform withdrawal from a customer balance:
- **Connect wallet:** ZenCrypto reads the public address and public balances.
- **Swap:** tokens move directly according to the reviewed smart-contract transaction signed by the user.
- **Send out / withdraw:** the user initiates a normal transfer from their wallet, or uses a reviewed contract exit path if the protocol actually locks assets.
- **Liquidity provision (if later offered):** a separate product with its own contract, risk disclosures and legal review.

Do not display fake deposit addresses, credit an internal balance from a submitted transaction hash alone, or label a transfer “complete” before verifying the canonical receipt and required confirmations. Do not call a wallet connection a deposit.

## 5. Contract and key-management requirements

- Use established, reviewed contract libraries and a minimal, documented contract surface.
- Prefer immutable contracts where practical. If upgradeability is essential, disclose the upgrade authority, use a timelock and multisig, and document emergency powers.
- No single operator key may move user assets or arbitrarily change balances.
- Separate deployer, pauser and governance roles; use hardware-backed keys and multisig controls.
- Emergency pause must have a narrow, documented scope and must not allow an operator to seize user assets.
- Verify source code and deployed bytecode; publish exact chain IDs and contract addresses.
- Complete independent smart-contract audits, fuzzing, invariant tests, static analysis, testnet exercises and a public bug-bounty process before mainnet.
- Never place private keys, seed phrases, signing secrets or RPC credentials in frontend code, GitHub, logs or browser storage.

## 6. U.S. market legal and product gates

Self-custody is a technical property, not a blanket regulatory exemption. U.S. analysis must be based on the actual functions, control, fees, governance, assets, counterparties and user locations:
- Obtain a written federal and state-by-state legal analysis for the exact interface, protocol, fee model and supported assets.
- Review whether any asset or transaction is a security and whether the interface or operator activity triggers exchange, broker-dealer, ATS or other securities obligations.
- Review CFTC/commodities and derivatives rules; exclude leverage, margin, futures and options from the initial scope.
- Review FinCEN/BSA/AML, sanctions, money-transmission and other state requirements for the actual activities performed. Do not assume a non-custodial label resolves them.
- Implement geofencing, sanctions controls, token eligibility and access restrictions only as counsel directs; do not claim nationwide availability until reviewed.
- Publish accurate terms, privacy disclosures, fee disclosures, risk warnings, incident contact and complaint process.
- Do not publish registration or licence badges unless the exact legal entity, regulator, number, permitted activity, jurisdiction and active status are verified.

Reference material for counsel and engineering review:
- SEC Trading and Markets crypto-asset FAQ: https://www.sec.gov/rules-regulations/staff-guidance/trading-markets-frequently-asked-questions/frequently-asked-questions-relating-crypto-asset-activities-distributed-ledger-technology
- FinCEN / Federal Register digital-asset broker rules and discussion of non-custodial participants: https://www.govinfo.gov/content/pkg/FR-2024-07-09/pdf/2024-14004.pdf
- U.S. Treasury digital-asset platform overview: https://home.treasury.gov/system/files/136/CryptoAsset_EO5.pdf

## 7. Current repository status (important)

Implemented in the current branch:
- Browser-injected wallet connection (EIP-1193).
- Public account address and chain ID display.
- Read-only native ETH balance lookup.
- Account and network change listeners.

Not implemented:
- No production swap router or pool contracts are deployed.
- No audited smart contracts, token approvals, swap signing or mainnet execution.
- No on-chain deposit/withdrawal product or internal customer custody.
- No production indexer, reorg-safe transaction monitor or settlement service.
- Direct wallet transfer is not a DEX swap and does not create a ZenCrypto exchange balance.
- No verified token allowlist, price oracle or production quote service.
- No legal determination or authorization for any U.S. state or asset.

The existing email/password accounts, paper USD balances, simulated orders and sandbox deposit/withdrawal screens remain a separate prototype. They must not be represented as connected to the user's self-custody wallet or as real trading. The legacy FastAPI/SQLite paper-trading backend remains separate from the self-custody frontend and must be removed or isolated before production.

## 8. Release gates

Mainnet swaps remain disabled until all of the following are complete:
1. Written legal approval for the selected U.S. product model, assets, states and access policy.
2. Contract specification, threat model, external audit and remediation sign-off.
3. Contract deployment and bytecode/source verification at documented addresses.
4. RPC redundancy, indexer reorg handling, transaction simulation and monitoring.
5. Frontend chain/token allowlists, slippage and price-impact limits, allowance warnings and transaction simulation.
6. Independent end-to-end testing, incident response and emergency-pause drills.
7. Production release approval and public disclosures.

A connected wallet or green CI build is not evidence that a DEX is live, secure, audited or legally authorized.
