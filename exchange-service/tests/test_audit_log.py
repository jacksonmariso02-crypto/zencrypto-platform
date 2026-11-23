import sqlite3
import tempfile
import unittest
from pathlib import Path

from audit_log import AuditLog


class AuditLogTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = str(Path(self.tmp.name) / "audit.sqlite3")
        self.audit = AuditLog(self.db_path)

    def tearDown(self):
        self.tmp.cleanup()

    def test_append_and_verify_hash_chain(self):
        first = self.audit.append(
            actor_role="owner", action="owner_console.read",
            resource="overview", metadata={"source": "test"},
        )
        second = self.audit.append(
            actor_role="compliance", action="owner_console.read",
            resource="audit", outcome="success",
        )
        report = self.audit.verify_chain()
        self.assertTrue(report["healthy"])
        self.assertEqual(report["events_checked"], 2)
        self.assertNotEqual(first["event_hash"], second["event_hash"])
        listed = self.audit.list_events(limit=10)
        self.assertEqual(listed["count"], 2)
        self.assertEqual(listed["events"][0]["actor_role"], "compliance")

    def test_update_and_delete_are_blocked(self):
        self.audit.append(actor_role="owner", action="test", resource="unit")
        with sqlite3.connect(self.db_path) as db:
            with self.assertRaises(sqlite3.IntegrityError):
                db.execute("UPDATE admin_audit_events SET outcome='tampered' WHERE sequence=1")
            with self.assertRaises(sqlite3.IntegrityError):
                db.execute("DELETE FROM admin_audit_events WHERE sequence=1")

    def test_hash_chain_detects_privileged_tampering(self):
        self.audit.append(actor_role="owner", action="test", resource="unit")
        with sqlite3.connect(self.db_path) as db:
            db.execute("DROP TRIGGER admin_audit_no_update")
            db.execute("UPDATE admin_audit_events SET outcome='tampered' WHERE sequence=1")
        report = self.audit.verify_chain()
        self.assertFalse(report["healthy"])
        self.assertEqual(report["issues"][0]["reason"], "hash_chain_mismatch")

    def test_security_summary_counts_auth_and_api_key_lifecycle_without_secrets(self):
        self.audit.append(
            actor_role="customer", action="customer.auth.login",
            resource="customer_session", outcome="failed",
            metadata={"user_id": "user-123", "ip_fingerprint": "abc123"},
        )
        self.audit.append(
            actor_role="customer", action="customer.auth.login",
            resource="customer_session", outcome="success",
            metadata={"user_id": "user-456"},
        )
        self.audit.append(
            actor_role="customer", action="customer.exchange_api_key.connected",
            resource="binance_api_credential", outcome="success",
            metadata={"masked_api_key_suffix": "1234"},
        )
        report = self.audit.security_summary(hours=24, limit=10)
        self.assertEqual(report["login_failures"], 1)
        self.assertEqual(report["login_successes"], 1)
        self.assertEqual(report["api_key_connections"], 1)
        self.assertEqual(len(report["recent_events"]), 3)
        self.assertIn("Device fingerprinting", " ".join(report["observability_limitations"]))

    def test_pagination_and_action_filter(self):
        self.audit.append(actor_role="owner", action="one", resource="a")
        self.audit.append(actor_role="owner", action="two", resource="b")
        page = self.audit.list_events(limit=1, action="two")
        self.assertEqual(page["count"], 1)
        self.assertEqual(page["events"][0]["action"], "two")


if __name__ == "__main__":
    unittest.main()
