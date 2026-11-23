import json
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch
import hashlib
import hmac

from partner_execution import (
    DuplicateEventConflict,
    LiveExecutionDisabled,
    NormalizedOrderStatus,
    OrderSide,
    OrderType,
    PartnerExecutionInbox,
    PartnerNotConfigured,
    PartnerOrderRequest,
    UnconfiguredExecutionPartner,
    assert_live_execution_gates,
    normalize_order_status,
    verify_webhook_signature,
)


class PartnerExecutionTests(unittest.TestCase):
    def test_limit_order_requires_positive_price(self):
        with self.assertRaises(ValueError):
            PartnerOrderRequest(
                "client-1", "BTC-USD", OrderSide.BUY, OrderType.LIMIT,
                Decimal("0.1"), None
            )

    def test_market_order_rejects_limit_price(self):
        with self.assertRaises(ValueError):
            PartnerOrderRequest(
                "client-1", "BTC-USD", OrderSide.BUY, OrderType.MARKET,
                Decimal("0.1"), Decimal("100")
            )

    def test_unconfigured_partner_fails_closed(self):
        import asyncio
        with self.assertRaises(PartnerNotConfigured):
            asyncio.run(
                UnconfiguredExecutionPartner().submit_order(
                    PartnerOrderRequest(
                        "client-1", "BTC-USD", OrderSide.BUY,
                        OrderType.MARKET, Decimal("0.1")
                    )
                )
            )

    def test_live_gates_default_to_disabled(self):
        with patch.dict("os.environ", {}, clear=True):
            with self.assertRaises(LiveExecutionDisabled):
                assert_live_execution_gates()

    def test_status_normalization(self):
        self.assertEqual(normalize_order_status("PARTIALLY_FILLED"),
                         NormalizedOrderStatus.PARTIALLY_FILLED)
        self.assertEqual(normalize_order_status("unrecognized"),
                         NormalizedOrderStatus.UNKNOWN)

    def test_webhook_signature_and_replay_window(self):
        body = b'{"event":"execution"}'
        secret = "test-secret"
        timestamp = "1700000000"
        signature = hmac.new(
            secret.encode(), timestamp.encode() + b"." + body, hashlib.sha256
        ).hexdigest()
        self.assertTrue(verify_webhook_signature(
            body, timestamp, signature, secret,
            now_seconds=1700000000, tolerance_seconds=300
        ))
        self.assertFalse(verify_webhook_signature(
            body, timestamp, signature, secret,
            now_seconds=1700001000, tolerance_seconds=300
        ))

    def test_inbox_is_idempotent_and_detects_payload_conflict(self):
        with tempfile.TemporaryDirectory() as directory:
            database = str(Path(directory) / "inbox.sqlite3")
            inbox = PartnerExecutionInbox(database)
            payload = {"execution_id": "exec-1", "quantity": "0.1"}
            self.assertTrue(inbox.record("partner-x", "event-1", payload))
            self.assertFalse(inbox.record("partner-x", "event-1", payload))
            with self.assertRaises(DuplicateEventConflict):
                inbox.record("partner-x", "event-1", {
                    "execution_id": "exec-1", "quantity": "0.2"
                })


if __name__ == "__main__":
    unittest.main()
