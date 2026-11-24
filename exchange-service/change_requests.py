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



REVIEW_EVIDENCE_REQUIREMENTS = {
    "customer_account_review": (
        "Verified customer identity and authorization",
        "Documented account risk assessment",
        "Independent reviewer identity with MFA",
        "Signed and retained review decision",
    ),
    "staff_access_review": (
        "Verified workforce identity and MFA",
        "Least-privilege access justification",
        "Independent approver distinct from requester",
        "Access expiration and revocation plan",
    ),
    "configuration_change_review": (
        "Proposed change diff and rollback plan",
        "Security and operational impact assessment",
        "Independent change approval with signed evidence",
        "Test environment verification and release gate",
    ),
    "kyc_policy_review": (
        "Regulatory counsel and compliance sign-off",
        "Documented KYC/AML policy version",
        "Independent reviewer with individual identity",
        "Evidence retention and customer privacy assessment",
    ),
    "security_incident_review": (
        "Incident reference and scope assessment",
        "Evidence handling and access controls",
        "Independent incident reviewer identity",
        "Containment and recovery plan",
    ),
}


def review_readiness(request_type):
    """Policy design checklist only. Never infer approval from a proposal."""
    if request_type not in REVIEW_EVIDENCE_REQUIREMENTS:
        raise ValueError("Unsupported request type.")
    return {
        "request_type": request_type,
        "required_evidence": list(REVIEW_EVIDENCE_REQUIREMENTS[request_type]),
        "verified_evidence_count": 0,
        "review_ready": False,
        "approval_granted": False,
        "execution_permitted": False,
        "blocking_reasons": [
            "No per-person reviewer identity or MFA verification is integrated.",
            "No independent signed decision evidence is collected.",
            "No executable approval or operational action is implemented.",
        ],
    }


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
            "requests": [{**dict(row), "review_readiness": review_readiness(row["request_type"])} for row in rows],
            "approvals_enabled": False,
            "execution_enabled": False,
            "limitations": "This is a review-request register, not a KYC, dual-approval, account-control or configuration-execution system.",
        }
