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
            CREATE TABLE wallet_ledger_entries(id INTEGER PRIMARY KEY,journal_id TEXT,user_id TEXT,asset TEXT,account_code TEXT,direction TEXT,amount TEXT,created_at INTEGER);
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

if __name__=="__main__": unittest.main()
