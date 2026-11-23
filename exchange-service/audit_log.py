"""Append-only, hash-chained audit event store for administrative activity."""
from __future__ import annotations

import hashlib
import json
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Any


class AuditLog:
    """SQLite-backed audit trail with hash chaining and mutation-blocking triggers.

    This is tamper-evident within the database, not WORM storage: a privileged
    database operator could still replace the database or drop triggers. Export
    hashes to independently controlled immutable storage for stronger assurance.
    """

    GENESIS_HASH = "0" * 64

    def __init__(self, database_path: str):
        self.database_path = str(database_path)
        if self.database_path != ":memory:":
            Path(self.database_path).parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            db.execute("""
                CREATE TABLE IF NOT EXISTS admin_audit_events (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_id TEXT NOT NULL UNIQUE,
                    occurred_at INTEGER NOT NULL,
                    actor_role TEXT NOT NULL,
                    action TEXT NOT NULL,
                    resource TEXT NOT NULL,
                    outcome TEXT NOT NULL,
                    metadata_json TEXT NOT NULL,
                    previous_hash TEXT NOT NULL,
                    event_hash TEXT NOT NULL UNIQUE
                )
            """)
            db.execute("""
                CREATE TRIGGER IF NOT EXISTS admin_audit_no_update
                BEFORE UPDATE ON admin_audit_events
                BEGIN SELECT RAISE(ABORT, 'audit events are append-only'); END
            """)
            db.execute("""
                CREATE TRIGGER IF NOT EXISTS admin_audit_no_delete
                BEFORE DELETE ON admin_audit_events
                BEGIN SELECT RAISE(ABORT, 'audit events are append-only'); END
            """)

    def _connect(self):
        db = sqlite3.connect(self.database_path, timeout=15, isolation_level=None)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA busy_timeout=15000")
        return db

    @staticmethod
    def _canonical(value: dict[str, Any]) -> str:
        return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)

    def append(self, *, actor_role: str, action: str, resource: str,
               outcome: str = "success", metadata: dict[str, Any] | None = None) -> dict:
        safe_metadata = metadata or {}
        if not isinstance(safe_metadata, dict):
            raise ValueError("metadata must be an object")
        event = {
            "event_id": str(uuid.uuid4()),
            "occurred_at": int(time.time()),
            "actor_role": str(actor_role)[:32],
            "action": str(action)[:96],
            "resource": str(resource)[:160],
            "outcome": str(outcome)[:32],
            "metadata": safe_metadata,
        }
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            previous = db.execute(
                "SELECT event_hash FROM admin_audit_events ORDER BY sequence DESC LIMIT 1"
            ).fetchone()
            previous_hash = previous["event_hash"] if previous else self.GENESIS_HASH
            digest_input = {"event": event, "previous_hash": previous_hash}
            event_hash = hashlib.sha256(self._canonical(digest_input).encode("utf-8")).hexdigest()
            db.execute("""
                INSERT INTO admin_audit_events
                (event_id, occurred_at, actor_role, action, resource, outcome,
                 metadata_json, previous_hash, event_hash)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (event["event_id"], event["occurred_at"], event["actor_role"],
                  event["action"], event["resource"], event["outcome"],
                  self._canonical(safe_metadata), previous_hash, event_hash))
            sequence = db.execute("SELECT last_insert_rowid()").fetchone()[0]
            db.execute("COMMIT")
        return {"sequence": sequence, "event_id": event["event_id"], "event_hash": event_hash}

    def list_events(self, *, limit: int = 100, before_sequence: int | None = None,
                    action: str | None = None) -> dict:
        limit = max(1, min(int(limit), 250))
        clauses, args = [], []
        if before_sequence is not None:
            clauses.append("sequence < ?")
            args.append(int(before_sequence))
        if action:
            clauses.append("action = ?")
            args.append(str(action)[:96])
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        with self._connect() as db:
            rows = db.execute(
                "SELECT sequence,event_id,occurred_at,actor_role,action,resource,outcome,"
                "metadata_json,previous_hash,event_hash FROM admin_audit_events"
                + where + " ORDER BY sequence DESC LIMIT ?", (*args, limit)
            ).fetchall()
            events = [{
                "sequence": row["sequence"], "event_id": row["event_id"],
                "occurred_at": row["occurred_at"], "actor_role": row["actor_role"],
                "action": row["action"], "resource": row["resource"],
                "outcome": row["outcome"], "metadata": json.loads(row["metadata_json"]),
                "previous_hash": row["previous_hash"], "event_hash": row["event_hash"],
            } for row in rows]
            return {
                "mode": "READ_ONLY_APPEND_ONLY_AUDIT_LOG",
                "count": len(events), "events": events,
                "next_before_sequence": events[-1]["sequence"] if len(events) == limit else None,
                "storage_assurance": "SQLite append-only triggers and SHA-256 hash chain; not external WORM storage",
            }

    def security_summary(self, *, hours: int = 24, limit: int = 50) -> dict:
        """Summarize observed customer authentication and exchange-key lifecycle events."""
        hours = max(1, min(int(hours), 168))
        limit = max(1, min(int(limit), 100))
        cutoff = int(time.time()) - hours * 3600
        actions = (
            "customer.auth.login", "customer.auth.register",
            "customer.exchange_api_key.connected",
            "customer.exchange_api_key.disconnected",
        )
        placeholders = ",".join("?" for _ in actions)
        with self._connect() as db:
            totals = db.execute(
                f"SELECT action,outcome,COUNT(*) AS n FROM admin_audit_events "
                f"WHERE occurred_at >= ? AND action IN ({placeholders}) "
                "GROUP BY action,outcome ORDER BY action,outcome",
                (cutoff, *actions),
            ).fetchall()
            rows = db.execute(
                f"SELECT sequence,event_id,occurred_at,actor_role,action,resource,outcome,"
                f"metadata_json,event_hash FROM admin_audit_events "
                f"WHERE occurred_at >= ? AND action IN ({placeholders}) "
                "ORDER BY sequence DESC LIMIT ?",
                (cutoff, *actions, limit),
            ).fetchall()
        by_action = {}
        for row in totals:
            by_action.setdefault(row["action"], {})[row["outcome"]] = int(row["n"])
        recent = [{
            "sequence": row["sequence"], "event_id": row["event_id"],
            "occurred_at": row["occurred_at"], "actor_role": row["actor_role"],
            "action": row["action"], "resource": row["resource"],
            "outcome": row["outcome"], "metadata": json.loads(row["metadata_json"]),
            "event_hash": row["event_hash"],
        } for row in rows]
        login_failures = by_action.get("customer.auth.login", {}).get("failed", 0)
        login_success = by_action.get("customer.auth.login", {}).get("success", 0)
        return {
            "mode": "OBSERVED_SECURITY_TELEMETRY",
            "window_hours": hours,
            "generated_at": int(time.time()),
            "event_totals": by_action,
            "login_successes": login_success,
            "login_failures": login_failures,
            "api_key_connections": by_action.get("customer.exchange_api_key.connected", {}).get("success", 0),
            "api_key_disconnections": by_action.get("customer.exchange_api_key.disconnected", {}).get("success", 0),
            "recent_events": recent,
            "observability_limitations": [
                "Failed authentication attempts blocked by the pre-authentication rate limiter are not included in this event stream.",
                "IP values are represented by a keyed fingerprint, not a raw address; reverse proxy trust and geolocation are not configured here.",
                "Device fingerprinting, impossible-travel detection, bot reputation and credential stuffing correlation are not implemented.",
                "API key events record connect/revoke actions only; key values and secrets are never stored in audit metadata.",
                "No automatic account lock, key revocation or fund movement is performed by this report.",
            ],
        }

    def verify_chain(self) -> dict:
        with self._connect() as db:
            rows = db.execute(
                "SELECT sequence,event_id,occurred_at,actor_role,action,resource,outcome,"
                "metadata_json,previous_hash,event_hash FROM admin_audit_events ORDER BY sequence"
            ).fetchall()
        previous_hash = self.GENESIS_HASH
        issues = []
        for row in rows:
            event = {
                "event_id": row["event_id"], "occurred_at": row["occurred_at"],
                "actor_role": row["actor_role"], "action": row["action"],
                "resource": row["resource"], "outcome": row["outcome"],
                "metadata": json.loads(row["metadata_json"]),
            }
            expected = hashlib.sha256(self._canonical({
                "event": event, "previous_hash": previous_hash
            }).encode("utf-8")).hexdigest()
            if row["previous_hash"] != previous_hash or row["event_hash"] != expected:
                issues.append({"sequence": row["sequence"], "reason": "hash_chain_mismatch"})
            previous_hash = row["event_hash"]
        return {
            "healthy": not issues, "status": "healthy" if not issues else "review_required",
            "events_checked": len(rows), "issue_count": len(issues), "issues": issues[:100],
            "head_hash": previous_hash, "verified_at": int(time.time()),
        }
