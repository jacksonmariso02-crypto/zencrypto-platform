"""Least-privilege bearer-token authorization for read-only Owner operations."""
from __future__ import annotations
import hmac
from typing import Mapping


ROLE_PERMISSIONS = {
    "owner": frozenset({"overview", "orders", "ledger", "reconciliation"}),
    "finance": frozenset({"overview", "ledger", "reconciliation"}),
    "compliance": frozenset({"overview", "orders", "ledger", "reconciliation"}),
}


class OwnerAuthorizationError(PermissionError):
    pass


def resolve_owner_role(authorization: str | None, tokens: Mapping[str, str]) -> str:
    supplied = (authorization or "").removeprefix("Bearer ").strip()
    if not supplied:
        raise OwnerAuthorizationError("Owner authorization required.")
    configured = {
        "owner": tokens.get("owner", ""),
        "finance": tokens.get("finance", ""),
        "compliance": tokens.get("compliance", ""),
    }
    # Backward-compatible legacy admin token maps to the full Owner role only.
    configured["owner"] = configured["owner"] or tokens.get("legacy_admin", "")
    if not any(configured.values()):
        raise RuntimeError("Configure at least one Owner Console role token.")
    for role, expected in configured.items():
        if expected and hmac.compare_digest(supplied, expected):
            return role
    raise OwnerAuthorizationError("Unauthorized.")


def require_owner_permission(authorization: str | None, permission: str,
                             tokens: Mapping[str, str]) -> str:
    role = resolve_owner_role(authorization, tokens)
    if permission not in ROLE_PERMISSIONS[role]:
        raise OwnerAuthorizationError("This role is not authorized for the requested Owner Console resource.")
    return role
