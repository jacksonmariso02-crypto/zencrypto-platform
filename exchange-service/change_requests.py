"""Owner change-request registry: proposals only, never execution or authorization.

This registry deliberately does not implement approvals, account freezing,
role changes, KYC decisions, credential rotation, or configuration mutations.
"""
import sqlite3
import time
import uuid
from contextlib import closing
from pathlib import Path

ALLOWED_TYPES = frozenset({
    "customer_account_review",
    "staff_access_review",
    "configuration_change_review",
    "kyc_policy_review",
    "security_incident_review",
})


class ChangeRequestRegistry:
    def __init__(self, db_path):
        self.db_path = str(db_path)
        if self.db_path != ":memory:":
            Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("""
                CREATE TABLE IF NOT EXISTS owner_change_requests (
                    request_id TEXT PRIMARY KEY,
                    request_type TEXT NOT NULL,
                    summary TEXT NOT NULL,
                    rationale TEXT NOT NULL,
                    status TEXT NOT NULL CHECK(status='pending_review'),
                    created_at INTEGER NOT NULL
                )
            """)
            db.execute("""
                CREATE INDEX IF NOT EXISTS idx_owner_change_requests_created
                ON owner_change_requests(created_at DESC, request_id DESC)
            """)

    def _connect(self):
        db = sqlite3.connect(self.db_path, timeout=15)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA busy_timeout=15000")
        return db

    def create(self, request_type, summary, rationale):
        if request_type not in ALLOWED_TYPES:
            raise ValueError("Unsupported request type.")
        if not isinstance(summary, str) or not 8 <= len(summary.strip()) <= 160:
            raise ValueError("Summary must contain 8–160 characters.")
        if not isinstance(rationale, str) or not 16 <= len(rationale.strip()) <= 1000:
            raise ValueError("Rationale must contain 16–1000 characters.")
        item = {
            "request_id": uuid.uuid4().hex,
            "request_type": request_type,
            "summary": summary.strip(),
            "rationale": rationale.strip(),
            "status": "pending_review",
            "created_at": int(time.time()),
        }
        with closing(self._connect()) as db:
            db.execute("""
                INSERT INTO owner_change_requests
                (request_id, request_type, summary, rationale, status, created_at)
                VALUES (:request_id, :request_type, :summary, :rationale, :status, :created_at)
            """, item)
            db.commit()
        return {**item, "execution_performed": False, "approval_granted": False}

    def list_requests(self, limit=50, offset=0):
        limit = int(limit)
        offset = int(offset)
        if not 1 <= limit <= 100 or offset < 0:
            raise ValueError("Invalid pagination parameters.")
        with closing(self._connect()) as db:
            total = db.execute("SELECT COUNT(*) FROM owner_change_requests").fetchone()[0]
            rows = db.execute("""
                SELECT request_id, request_type, summary, rationale, status, created_at
                FROM owner_change_requests ORDER BY created_at DESC, request_id DESC
                LIMIT ? OFFSET ?
            """, (limit, offset)).fetchall()
        return {
            "mode": "PROPOSAL_ONLY_NO_EXECUTION",
            "total": total, "limit": limit, "offset": offset,
            "requests": [dict(row) for row in rows],
            "approvals_enabled": False,
            "execution_enabled": False,
            "limitations": "This is a review-request register, not a KYC, dual-approval, account-control or configuration-execution system.",
        }
