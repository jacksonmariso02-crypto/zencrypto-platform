# Wallet transfer implementation

## Implemented in the current frontend branch

The wallet page uses the EIP-1193 provider injected by a browser wallet:
- Connects by requesting public accounts.
- Reads the active chain ID and wallet's native ETH balance using `eth_getBalance`.
- Reads WETH and USDC balances on Ethereum mainnet and Base mainnet using `eth_call(balanceOf)` against the configured token contracts.
- Switches between Ethereum, Base, Sepolia and Base Sepolia using `wallet_switchEthereumChain`; it can request addition of Base and supported test networks when not present.
- Shows the connected wallet address as the receive address and provides a copy action.
- Sends native ETH using `eth_sendTransaction`.
- Sends WETH and USDC using the ERC-20 `transfer(address,uint256)` method via `eth_sendTransaction`.
- Wraps ETH into WETH through the WETH `deposit()` method and unwraps WETH through `withdraw(uint256)`, both signed by the user's wallet.
- Displays the submitted transaction hash, a chain-specific block explorer link, and a user-triggered receipt status lookup.

The app does not request, receive or store seed phrases or private keys. The connected wallet presents transaction details and asks the user to sign. A successful wallet submission is not the same as a confirmed transaction; the UI distinguishes submitted from mined/reverted receipt status.

## Supported networks and token configuration

| Network | Chain ID | Native asset | USDC |
|---|---:|---|---|
| Ethereum Mainnet | 1 | ETH | Canonical USDC contract configured |
| Base Mainnet | 8453 | ETH | Canonical USDC contract configured |
| Sepolia | 11155111 | Test ETH | Not configured |
| Base Sepolia | 84532 | Test ETH | Not configured |

Before production release, verify all chain IDs and token contract addresses against issuer documentation and chain explorers, and maintain a versioned allowlist. The current UI intentionally does not accept arbitrary token contract addresses.

## DEX quote and swap integration added

The backend now provides a server-side 0x Swap API v2 proxy for indicative price and firm quotes. The proxy supports only WETH/USDC on Ethereum Mainnet and Base Mainnet, validates the token pair and wallet address, and rejects an unexpected AllowanceHolder spender. The 0x API key is read only from the backend environment variable `ZEROX_API_KEY`.

The frontend can request an indicative price, fetch a firm quote, request an exact ERC-20 allowance through the connected wallet when the provider reports it is needed, and submit the returned transaction through the wallet. The configured maximum slippage is 100 basis points (1%). The flow waits for an approval receipt before requesting a fresh firm quote.

This integration requires a valid 0x API key on the backend and has not yet been tested with live mainnet funds. It is not a claim of audit or production readiness.

## What is not implemented

- Independent quote-response signature verification and provider security assessment.
- Swap router or liquidity pool contracts.
- ERC-20 approval workflow for swaps.
- Swap transaction simulation, slippage and price-impact limits.
- Indexer, confirmations policy, reorg handling and durable transaction history.
- Contract security audit, bug bounty, monitoring and incident response.
- Any ZenCrypto-operated deposit address, internal customer balance, custody, or hosted withdrawal service.

## Safe operating guidance

1. Use test networks and valueless test assets while validating the wallet interaction and UI.
2. For any mainnet transfer, confirm the selected network, recipient, asset, amount and wallet gas estimate before signing.
3. Start with a small amount. Blockchain transfers are generally irreversible.
4. Never paste a seed phrase or private key into the site or support chat.
5. Treat the transaction hash as submitted until the receipt is mined; wait for an appropriate confirmation/finality policy before describing the transfer as final.
6. A wallet-to-wallet transfer is not an exchange swap. The DEX feature must remain disabled until its own contracts, quote engine, simulation and audits are complete.
