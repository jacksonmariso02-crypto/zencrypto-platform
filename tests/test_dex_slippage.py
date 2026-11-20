import sys
from pathlib import Path

import pytest
from pydantic import ValidationError
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
import main


def test_firm_quote_rejects_slippage_outside_supported_range():
    base = {
        "chainId": 1,
        "sellToken": "0x" + "1" * 40,
        "buyToken": "0x" + "2" * 40,
        "sellAmount": "10000000000000000",
        "taker": "0x" + "3" * 40,
    }
    for value in (9, 301, -1):
        with pytest.raises(ValidationError):
            main.DexQuoteRequest(**base, slippageBps=value)


def test_indicative_price_rejects_slippage_above_three_percent():
    with TestClient(main.app) as client:
        response = client.get(
            "/dex/price",
            params={
                "chainId": 1,
                "sellToken": "0x" + "1" * 40,
                "buyToken": "0x" + "2" * 40,
                "sellAmount": "10000000000000000",
                "taker": "0x" + "3" * 40,
                "slippageBps": 301,
            },
        )
    assert response.status_code == 422
    assert "between 10 and 300" in response.json()["detail"]
