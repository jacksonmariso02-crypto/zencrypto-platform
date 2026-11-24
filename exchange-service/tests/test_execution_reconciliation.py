import sqlite3, tempfile, unittest
from pathlib import Path
from execution_reconciliation import ExecutionEventConflict, ExecutionReconciliation

class ExecutionReconciliationTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.path=str(Path(self.tmp.name)/"exec.sqlite3")
        with sqlite3.connect(self.path) as db:
            db.execute("""CREATE TABLE customer_order_requests(
                user_id TEXT,idempotency_key TEXT,status TEXT,response_json TEXT,updated_at INTEGER)""")
            db.execute("""INSERT INTO customer_order_requests VALUES(
                'user-1','idem-1','SUBMITTED',
                '{"exchange":"partner-x","exchange_order_id":"order-1","filled":"0.5","cost":"50"}',100)""")
        self.service=ExecutionReconciliation(self.path)
    def tearDown(self): self.tmp.cleanup()
    def event(self, **updates):
        data={"provider":"partner-x","provider_event_id":"event-1","partner_order_id":"order-1",
            "execution_id":"fill-1","symbol":"BTC/USDT","side":"BUY","quantity":"0.5",
            "price":"100","fee_amount":"0.01","fee_asset":"USDT","executed_at_ms":1700000000000}
        data.update(updates)
        return data
    def test_records_and_deduplicates_exact_execution(self):
        self.assertTrue(self.service.record_execution(self.event())["recorded"])
        self.assertTrue(self.service.record_execution(self.event())["duplicate"])
    def test_conflicting_event_reuse_is_rejected(self):
        self.service.record_execution(self.event())
        with self.assertRaises(ExecutionEventConflict):
            self.service.record_execution(self.event(quantity="0.6"))
    def test_same_execution_id_different_event_is_rejected(self):
        self.service.record_execution(self.event())
        with self.assertRaises(ExecutionEventConflict):
            self.service.record_execution(self.event(provider_event_id="event-2"))
    def test_invalid_numeric_data_is_rejected(self):
        with self.assertRaises(ValueError):
            self.service.record_execution(self.event(quantity="-1"))
    def test_reconciliation_matches_local_order(self):
        self.service.record_execution(self.event())
        report=self.service.reconciliation_report()
        self.assertEqual(report["matched_orders"],1)
        self.assertEqual(report["matched"][0]["status"],"matched")
        self.assertEqual(report["matched"][0]["fees_by_asset"]["USDT"],"0.01")
    def test_reconciliation_flags_quantity_mismatch(self):
        self.service.record_execution(self.event(quantity="0.4"))
        report=self.service.reconciliation_report()
        self.assertEqual(report["mismatched_orders"],1)
        self.assertEqual(report["matched"][0]["status"],"quantity_mismatch")
    def test_unmatched_execution_is_reported(self):
        self.service.record_execution(self.event(partner_order_id="orphan-order"))
        self.assertEqual(self.service.reconciliation_report()["unmatched_provider_orders"],1)

if __name__=="__main__": unittest.main()
