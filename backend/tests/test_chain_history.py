import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from chain_history import normalize_chain_history_items


WALLET = "0x" + "1" * 40
OTHER = "0x" + "2" * 40
TX = "0x" + "a" * 64
USDC = "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48"


def test_normalizes_native_transfer_and_direction():
    result = normalize_chain_history_items(
        1, WALLET,
        [{"hash": TX, "from": OTHER, "to": WALLET, "value": "1000000000000000000", "timestamp": "100", "blockNumber": "10"}],
        [],
    )
    item = result["items"][0]
    assert item["kind"] == "native_transfer"
    assert item["direction"] == "in"
    assert item["valueRaw"] == "1000000000000000000"
    assert item["value"] == "1"
    assert item["status"] == "indexed_unverified"


def test_normalizes_allowlisted_token_amount_without_float_rounding():
    result = normalize_chain_history_items(
        1, WALLET, [],
        [{"txHash": TX, "tokenAddress": USDC, "from": WALLET, "to": OTHER,
          "amount": "12.345678", "timestamp": 101, "blockNumber": 11, "logIndex": 4}],
    )
    item = result["items"][0]
    assert item["direction"] == "out"
    assert item["token"] == "USDC"
    assert item["contractVerified"] is True
    assert item["valueRaw"] == "12345678"
    assert item["eventId"] == TX + ":erc20:4"


def test_unknown_token_is_not_treated_as_allowlisted():
    unknown = "0x" + "3" * 40
    result = normalize_chain_history_items(
        8453, WALLET, [],
        [{"txHash": TX, "tokenAddress": unknown, "tokenSymbol": "FAKE",
          "tokenDecimals": 18, "amount": "1.25", "from": OTHER, "to": WALLET}],
    )
    item = result["items"][0]
    assert item["token"] == "FAKE"
    assert item["contractVerified"] is False
    assert item["valueRaw"] == "1250000000000000000"


def test_deduplicates_events_by_transaction_and_log_index():
    token_row = {"txHash": TX, "tokenAddress": USDC, "amount": "1.0",
                 "from": OTHER, "to": WALLET, "logIndex": 2}
    result = normalize_chain_history_items(
        1, WALLET,
        [{"hash": TX, "from": OTHER, "to": WALLET, "value": "0"}],
        [token_row, dict(token_row)],
    )
    assert len(result["items"]) == 2
    assert len({item["eventId"] for item in result["items"]}) == 2


@pytest.mark.parametrize(
    "chain,address,txs,tokens,limit",
    [
        (10, WALLET, [], [], 25),
        (1, "bad", [], [], 25),
        (1, WALLET, {}, [], 25),
        (1, WALLET, [], [], 0),
        (1, WALLET, [], [], 51),
    ],
)
def test_rejects_invalid_chain_history_inputs(chain, address, txs, tokens, limit):
    with pytest.raises(ValueError):
        normalize_chain_history_items(chain, address, txs, tokens, limit)



def test_accepts_string_decimals_and_hex_log_index():
    result = normalize_chain_history_items(
        8453, WALLET, [],
        [{"txHash": TX, "tokenAddress": "0x" + "3" * 40,
          "tokenSymbol": "TEST", "tokenDecimals": "6", "amount": "2.500001",
          "from": OTHER, "to": WALLET, "logIndex": "0x10"}],
    )
    item = result["items"][0]
    assert item["decimals"] == 6
    assert item["valueRaw"] == "2500001"
    assert item["eventId"] == TX + ":erc20:16"
