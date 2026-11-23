"""Partner-neutral execution boundary for ZenCrypto.

This module deliberately contains no live venue implementation. Until a provider,
its legal role, credentials, contract, and production controls are approved, the
gateway fails closed. Never interpret an internal acceptance as an exchange fill.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import sqlite3
import time
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from enum import Enum
from typing import Any, Protocol


class PartnerNotConfigured(RuntimeError):
    """Raised when no approved execution partner is configured."""


class LiveExecutionDisabled(RuntimeError):
    """Raised when production execution gates are not all enabled."""


class DuplicateEventConflict(RuntimeError):
    """A previously seen provider event ID was reused with different content."""


class OrderSide(str, Enum):
    BUY = "BUY"
    SELL = "SELL"


class OrderType(str, Enum):
    MARKET = "MARKET"
    LIMIT = "LIMIT"


class NormalizedOrderStatus(str, Enum):
    PENDING_SUBMISSION = "PENDING_SUBMISSION"
    SUBMITTED = "SUBMITTED"
    OPEN = "OPEN"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    CANCEL_PENDING = "CANCEL_PENDING"
    CANCELED = "CANCELED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class PartnerOrderRequest:
    client_order_id: str
    symbol: str
    side: OrderSide
    order_type: OrderType
    quantity: Decimal
    limit_price: Decimal | None = None
    time_in_force: str = "GTC"

    def __post_init__(self) -> None:
        if not self.client_order_id or len(self.client_order_id) > 128:
            raise ValueError("client_order_id must contain 1-128 characters")
        if not self.symbol or len(self.symbol) > 32:
            raise ValueError("symbol must contain 1-32 characters")
        if not self.quantity.is_finite() or self.quantity <= 0:
            raise ValueError("quantity must be finite and positive")
        if self.order_type is OrderType.LIMIT:
            if self.limit_price is None or not self.limit_price.is_finite() or self.limit_price <= 0:
                raise ValueError("limit orders require a finite positive limit_price")
        elif self.limit_price is not None:
            raise ValueError("market orders must not include limit_price")
        if self.time_in_force not in {"GTC", "IOC", "FOK"}:
            raise ValueError("unsupported time_in_force")


@dataclass(frozen=True)
class PartnerOrderAck:
    provider: str
    partner_order_id: str
    client_order_id: str
    status: NormalizedOrderStatus
    accepted_at_ms: int


@dataclass(frozen=True)
class NormalizedExecution:
    provider: str
    provider_event_id: str
    partner_order_id: str
    execution_id: str
    symbol: str
    side: OrderSide
    quantity: Decimal
    price: Decimal
    fee_amount: Decimal
    fee_asset: str
    executed_at_ms: int

    def __post_init__(self) -> None:
        for field_name, value in (
            ("quantity", self.quantity),
            ("price", self.price),
            ("fee_amount", self.fee_amount),
        ):
            if not value.is_finite():
                raise ValueError(f"{field_name} must be finite")
        if self.quantity <= 0 or self.price <= 0 or self.fee_amount < 0:
            raise ValueError("execution quantity/price must be positive and fee non-negative")
        if not self.provider_event_id or not self.execution_id or not self.partner_order_id:
            raise ValueError("provider event, execution and partner order IDs are required")


class ExecutionPartner(Protocol):
    """Implemented only by a reviewed, contracted and approved provider adapter."""

    provider_name: str

    async def submit_order(self, order: PartnerOrderRequest) -> PartnerOrderAck: ...
    async def cancel_order(self, partner_order_id: str, symbol: str) -> dict[str, Any]: ...
    async def get_order(self, partner_order_id: str, symbol: str) -> dict[str, Any]: ...


class UnconfiguredExecutionPartner:
    provider_name = "unconfigured"

    async def submit_order(self, order: PartnerOrderRequest) -> PartnerOrderAck:
        raise PartnerNotConfigured(
            "No approved execution partner is configured. Live order routing is unavailable."
        )

    async def cancel_order(self, partner_order_id: str, symbol: str) -> dict[str, Any]:
        raise PartnerNotConfigured("No approved execution partner is configured.")

    async def get_order(self, partner_order_id: str, symbol: str) -> dict[str, Any]:
        raise PartnerNotConfigured("No approved execution partner is configured.")


def build_execution_partner() -> ExecutionPartner:
    """Return a fail-closed adapter until a reviewed provider is implemented."""
    configured = os.getenv("PARTNER_EXECUTION_PROVIDER", "").strip()
    if configured:
        raise PartnerNotConfigured(
            f"Provider '{configured}' has no reviewed adapter implementation. "
            "Do not enable live routing."
        )
    return UnconfiguredExecutionPartner()


def assert_live_execution_gates() -> None:
    """Require independent deployment, provider and regulatory release gates."""
    required = (
        "LIVE_TRADING_ENABLED",
        "LIVE_CUSTOMER_ORDER_ROUTING_ENABLED",
        "PARTNER_PRODUCTION_APPROVED",
        "REGULATORY_LIVE_TRADING_APPROVED",
        "SECURITY_GO_LIVE_APPROVED",
    )
    missing = [name for name in required if os.getenv(name, "false").lower() != "true"]
    if missing:
        raise LiveExecutionDisabled(
            "Live execution is disabled; unmet release gates: " + ", ".join(missing)
        )


def normalize_order_status(raw_status: str) -> NormalizedOrderStatus:
    mapping = {
        "NEW": NormalizedOrderStatus.OPEN,
        "PENDING_NEW": NormalizedOrderStatus.SUBMITTED,
        "PARTIALLY_FILLED": NormalizedOrderStatus.PARTIALLY_FILLED,
        "FILLED": NormalizedOrderStatus.FILLED,
        "PENDING_CANCEL": NormalizedOrderStatus.CANCEL_PENDING,
        "CANCELED": NormalizedOrderStatus.CANCELED,
        "CANCELLED": NormalizedOrderStatus.CANCELED,
        "REJECTED": NormalizedOrderStatus.REJECTED,
        "EXPIRED": NormalizedOrderStatus.EXPIRED,
    }
    return mapping.get(str(raw_status).upper(), NormalizedOrderStatus.UNKNOWN)


def verify_webhook_signature(
    raw_body: bytes,
    timestamp: str,
    supplied_signature: str,
    secret: str,
    *,
    now_seconds: int | None = None,
    tolerance_seconds: int = 300,
) -> bool:
    """Verify timestamped HMAC-SHA256 webhook signatures.

    Expected signature format: hex HMAC-SHA256(timestamp + b'.' + raw_body).
    Provider-specific canonicalization must be confirmed before use.
    """
    if not secret or not timestamp or not supplied_signature:
        return False
    try:
        timestamp_int = int(timestamp)
    except (TypeError, ValueError):
        return False
    now = int(time.time()) if now_seconds is None else int(now_seconds)
    if abs(now - timestamp_int) > tolerance_seconds:
        return False
    signed = timestamp.encode("ascii") + b"." + raw_body
    expected = hmac.new(secret.encode("utf-8"), signed, hashlib.sha256).hexdigest()
    candidate = supplied_signature.removeprefix("sha256=").lower()
    return hmac.compare_digest(expected, candidate)


class PartnerExecutionInbox:
    """Durable webhook inbox with event-ID and payload-conflict protection.

    This records verified provider events only. It does not post customer ledger
    entries; a separately reconciled settlement workflow must do that.
    """

    def __init__(self, database_path: str):
        self.database_path = database_path
        with sqlite3.connect(self.database_path) as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS partner_execution_inbox (
                    provider TEXT NOT NULL,
                    provider_event_id TEXT NOT NULL,
                    payload_sha256 TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    received_at INTEGER NOT NULL,
                    processing_status TEXT NOT NULL DEFAULT 'RECEIVED',
                    PRIMARY KEY(provider, provider_event_id)
                )
                """
            )

    def record(self, provider: str, event_id: str, payload: dict[str, Any]) -> bool:
        if not provider or not event_id:
            raise ValueError("provider and event_id are required")
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        with sqlite3.connect(self.database_path, isolation_level="IMMEDIATE") as connection:
            existing = connection.execute(
                """SELECT payload_sha256 FROM partner_execution_inbox
                   WHERE provider=? AND provider_event_id=?""",
                (provider, event_id),
            ).fetchone()
            if existing:
                if not hmac.compare_digest(existing[0], digest):
                    raise DuplicateEventConflict(
                        "Provider event ID was reused with a different payload."
                    )
                return False
            connection.execute(
                """INSERT INTO partner_execution_inbox
                   (provider, provider_event_id, payload_sha256, payload_json, received_at)
                   VALUES (?, ?, ?, ?, ?)""",
                (provider, event_id, digest, canonical, int(time.time())),
            )
        return True
