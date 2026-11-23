import sqlite3, tempfile, unittest
from pathlib import Path
from owner_console import OwnerConsole

class OwnerConsoleTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.db_path=str(Path(self.tmp.name)/"owner.sqlite3")
        with sqlite3.connect(self.db_path) as db:
            db.executescript("""
            CREATE TABLE customer_order_requests(user_id TEXT,idempotency_key TEXT,exchange_client_order_id TEXT,status TEXT,response_json TEXT,created_at INTEGER,updated_at INTEGER);
            CREATE TABLE customer_exchange_connections(user_id TEXT,status TEXT);
            CREATE TABLE wallet_journals(journal_id TEXT,user_id TEXT,asset TEXT,event_type TEXT,status TEXT,idempotency_key TEXT,provider_reference TEXT,created_at INTEGER);
            CREATE TABLE wallet_ledger_entries(entry_id INTEGER PRIMARY KEY,journal_id TEXT,user_id TEXT,asset TEXT,account_code TEXT,direction TEXT,amount TEXT,created_at INTEGER);
            CREATE TABLE paper_orders(id TEXT); CREATE TABLE paper_trades(id TEXT); CREATE TABLE paper_accounts(id TEXT);
            INSERT INTO customer_order_requests VALUES('u1','idem-1','client-1','UNKNOWN',NULL,100,110);
            INSERT INTO customer_exchange_connections VALUES('u1','connected');
            INSERT INTO wallet_journals VALUES('j1','u1','USDT','test','posted','wallet-1',NULL,100);
            INSERT INTO wallet_ledger_entries VALUES(1,'j1','u1','USDT','customer_available','credit','10',100),(2,'j1','u1','USDT','clearing','debit','10',100);
            INSERT INTO paper_orders VALUES('po1'); INSERT INTO paper_trades VALUES('pt1'); INSERT INTO paper_accounts VALUES('pa1');
            """)
        self.console=OwnerConsole(self.db_path)
    def tearDown(self): self.tmp.cleanup()
    def test_overview(self):
        r=self.console.overview(partner_status={"provider":"unconfigured"},readiness={"ready":False})
        self.assertEqual(r["customer_order_requests"]["requiring_reconciliation"],1)
        self.assertEqual(r["customers_with_connected_exchange"],1)
        self.assertEqual(r["wallet_ledger"]["customer_available_net_by_asset"]["USDT"],"10")
        self.assertFalse(r["live_customer_order_routing_enabled"])
    def test_sensitive_fields_filtered(self):
        with sqlite3.connect(self.db_path) as db:
            db.execute("UPDATE customer_order_requests SET response_json=?",('{"symbol":"BTC/USDT","api_secret":"must-not-leak"}',))
        r=self.console.recent_orders()
        self.assertNotIn("api_secret",r["orders"][0]["order"])
    def test_balanced_ledger(self):
        self.assertTrue(self.console.ledger_integrity()["healthy"])
    def test_unbalanced_ledger(self):
        with sqlite3.connect(self.db_path) as db:
            db.execute("UPDATE wallet_ledger_entries SET amount='9' WHERE account_code='clearing'")
        self.assertEqual(self.console.ledger_integrity()["issues"][0]["reason"],"unbalanced")

    def test_risk_alerts_report_pending_orders_and_failed_readiness(self):
        report = self.console.risk_alerts(
            readiness={"ready": False, "checks": {"database": True, "provider": False}},
            reconciliation={"mismatched_orders": 0, "unmatched_provider_orders": 0},
        )
        self.assertEqual(report["risk_level"], "high")
        by_id = {alert["id"]: alert for alert in report["alerts"]}
        self.assertEqual(by_id["customer_orders_pending_reconciliation"]["evidence"]["count"], 1)
        self.assertEqual(by_id["platform_readiness_failed"]["evidence"]["failed_checks"], ["provider"])
        self.assertIn("No login-attempt", " ".join(report["limitations"]))

    def test_risk_alerts_escalate_ledger_and_execution_mismatches(self):
        with sqlite3.connect(self.db_path) as db:
            db.execute("UPDATE wallet_ledger_entries SET amount='9' WHERE account_code='clearing'")
        report = self.console.risk_alerts(
            readiness={"ready": True, "checks": {"database": True}},
            reconciliation={"mismatched_orders": 2, "unmatched_provider_orders": 1},
        )
        self.assertEqual(report["risk_level"], "critical")
        by_id = {alert["id"]: alert for alert in report["alerts"]}
        self.assertEqual(by_id["ledger_integrity_issues"]["severity"], "critical")
        self.assertEqual(by_id["execution_reconciliation_mismatch"]["evidence"]["mismatched_orders"], 2)
        self.assertIn("unmatched_provider_executions", by_id)

    def test_financial_summary_reports_only_simulated_fees_and_volume(self):
        import time
        now_ns = time.time_ns()
        now_s = time.time_ns()
        with sqlite3.connect(self.db_path) as db:
            db.executescript("""
            CREATE TABLE ledger_transactions(id TEXT PRIMARY KEY,idempotency_key TEXT,kind TEXT,memo TEXT,created_at INTEGER);
            CREATE TABLE ledger_entries(id INTEGER PRIMARY KEY,transaction_id TEXT,account_code TEXT,asset TEXT,debit TEXT,credit TEXT);
            ALTER TABLE paper_trades ADD COLUMN symbol TEXT;
            ALTER TABLE paper_trades ADD COLUMN price TEXT;
            ALTER TABLE paper_trades ADD COLUMN quantity TEXT;
            ALTER TABLE paper_trades ADD COLUMN created_at INTEGER;
            """)
            db.execute("INSERT INTO ledger_transactions VALUES('tx1','key-12345678','paper_trade_settlement','simulated',?)",(now_ns,))
            db.execute("INSERT INTO ledger_entries(transaction_id,account_code,asset,debit,credit) VALUES('tx1','platform:fees','USDT','0','1.25')")
            db.execute("INSERT INTO paper_trades(id,symbol,price,quantity,created_at) VALUES('test-trade','BTC/USDT','50000','0.01',?)",(now_s,))
        result = self.console.financial_summary(30)
        self.assertEqual(result["paper_trade_count"],1)
        self.assertEqual(result["paper_volume_by_quote_asset"]["USDT"],"500.00")
        self.assertEqual(result["simulated_fee_revenue_by_asset"]["USDT"],"1.25")
        self.assertEqual(result["classification"],"SIMULATED_PAPER_TRADING_ONLY_NOT_REAL_CUSTOMER_REVENUE_OR_CUSTODIED_ASSETS")

    def test_financial_summary_bounds_period(self):
        result = self.console.financial_summary(999)
        self.assertEqual(result["period_days"],90)


if __name__=="__main__": unittest.main()


