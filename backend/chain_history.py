from decimal import Decimal, InvalidOperation
import re


TOKEN_ALLOWLIST = {
    1: {
        "0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2": {"symbol": "WETH", "decimals": 18},
        "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48": {"symbol": "USDC", "decimals": 6},
    },
    8453: {
        "0x4200000000000000000000000000000000000006": {"symbol": "WETH", "decimals": 18},
        "0x833589fcd6edb6e08f4c7c32d4f71b54bdA02913".lower(): {"symbol": "USDC", "decimals": 6},
    },
}
ADDRESS_RE = re.compile(r"^0x[a-fA-F0-9]{40}$")
HASH_RE = re.compile(r"^0x[a-fA-F0-9]{64}$")


def _timestamp(value):
    if isinstance(value, bool):
        return None
    try:
        result = int(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return result if result >= 0 else None


def _raw_amount(value, decimals):
    if isinstance(value, bool) or decimals is None:
        return None
    try:
        amount = Decimal(str(value))
        if not amount.is_finite() or amount < 0:
            return None
        scaled = amount * (Decimal(10) ** decimals)
        if scaled != scaled.to_integral_value():
            return None
        return str(int(scaled))
    except (InvalidOperation, ValueError, TypeError, OverflowError):
        return None


def normalize_chain_history_items(chain_id, address, tx_items, token_items, limit=25):
    """Normalize indexed public activity. Provider rows are hints, not finalized receipts."""
    if chain_id not in TOKEN_ALLOWLIST:
        raise ValueError("Unsupported chain.")
    if not isinstance(address, str) or not ADDRESS_RE.fullmatch(address):
        raise ValueError("Invalid wallet address.")
    if not isinstance(tx_items, list) or not isinstance(token_items, list):
        raise ValueError("Provider items must be lists.")
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 50:
        raise ValueError("Limit must be between 1 and 50.")

    wallet = address.lower()
    items, seen = [], set()

    for index, row in enumerate(tx_items):
        if not isinstance(row, dict):
            continue
        tx_hash = row.get("hash") or row.get("txHash")
        if not isinstance(tx_hash, str) or not HASH_RE.fullmatch(tx_hash):
            continue
        sender, recipient = row.get("from"), row.get("to")
        sender = sender.lower() if isinstance(sender, str) and ADDRESS_RE.fullmatch(sender) else None
        recipient = recipient.lower() if isinstance(recipient, str) and ADDRESS_RE.fullmatch(recipient) else None
        raw_value = row.get("value", "0")
        try:
            value = int(raw_value, 16) if isinstance(raw_value, str) and raw_value.startswith(("0x", "0X")) else int(raw_value)
            if value < 0:
                continue
        except (TypeError, ValueError, OverflowError):
            continue
        timestamp = _timestamp(row.get("timestamp") or row.get("createdAt"))
        block_number = _timestamp(row.get("blockNumber"))
        event_id = f"{tx_hash.lower()}:native"
        if event_id in seen:
            continue
        seen.add(event_id)
        items.append({
            "eventId": event_id,
            "kind": "native_transfer" if value > 0 and sender and recipient else "transaction",
            "hash": tx_hash.lower(),
            "timestamp": timestamp,
            "from": sender,
            "to": recipient,
            "direction": "out" if sender == wallet else ("in" if recipient == wallet else "other"),
            "valueRaw": str(value),
            "value": str(Decimal(value) / (Decimal(10) ** 18)),
            "token": "ETH" if chain_id == 1 else "ETH",
            "decimals": 18,
            "status": "indexed_unverified",
            "blockNumber": block_number,
            "source": "Routescan",
        })

    for index, row in enumerate(token_items):
        if not isinstance(row, dict):
            continue
        tx_hash = row.get("txHash") or row.get("hash")
        if not isinstance(tx_hash, str) or not HASH_RE.fullmatch(tx_hash):
            continue
        contract = row.get("tokenAddress") or row.get("contractAddress")
        if not isinstance(contract, str) or not ADDRESS_RE.fullmatch(contract):
            continue
        contract_key = contract.lower()
        trusted = TOKEN_ALLOWLIST[chain_id].get(contract_key)
        decimals = trusted["decimals"] if trusted else row.get("tokenDecimals")
        if isinstance(decimals, str) and decimals.isascii() and decimals.isdigit():
            decimals = int(decimals)
        if isinstance(decimals, bool) or not isinstance(decimals, int) or not 0 <= decimals <= 36:
            decimals = None
        amount = row.get("amount")
        raw_amount = _raw_amount(amount, decimals) if decimals is not None else None
        sender, recipient = row.get("from"), row.get("to")
        sender = sender.lower() if isinstance(sender, str) and ADDRESS_RE.fullmatch(sender) else None
        recipient = recipient.lower() if isinstance(recipient, str) and ADDRESS_RE.fullmatch(recipient) else None
        timestamp = _timestamp(row.get("timestamp") or row.get("createdAt"))
        block_number = _timestamp(row.get("blockNumber"))
        log_index = row.get("logIndex", row.get("transferIndex", index))
        if isinstance(log_index, str) and log_index.startswith(("0x", "0X")):
            try:
                log_index = str(int(log_index, 16))
            except ValueError:
                log_index = index
        if isinstance(log_index, bool) or not isinstance(log_index, (str, int)) or not str(log_index).isdigit():
            log_index = index
        event_id = f"{tx_hash.lower()}:erc20:{log_index}"
        if event_id in seen:
            continue
        seen.add(event_id)
        items.append({
            "eventId": event_id,
            "kind": "token_transfer",
            "hash": tx_hash.lower(),
            "timestamp": timestamp,
            "from": sender,
            "to": recipient,
            "direction": "out" if sender == wallet else ("in" if recipient == wallet else "other"),
            "value": str(amount) if amount is not None else None,
            "valueRaw": raw_amount,
            "token": trusted["symbol"] if trusted else (row.get("tokenSymbol") or row.get("tokenName") or "Unknown token"),
            "decimals": decimals,
            "tokenAddress": contract_key,
            "contractVerified": bool(trusted),
            "status": "indexed_unverified",
            "blockNumber": block_number,
            "source": "Routescan",
        })

    items.sort(key=lambda item: (item["timestamp"] is not None, item["timestamp"] or -1, item["eventId"]), reverse=True)
    return {
        "chainId": chain_id,
        "address": wallet,
        "items": items[:limit],
        "source": "Routescan",
        "notice": "Indexed rows are not transaction receipts or proof of finality. Provider data can lag or omit activity; verify transaction status and block hash on-chain.",
    }
