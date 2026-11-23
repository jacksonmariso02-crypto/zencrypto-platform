"""Least-privilege bearer-token authorization for read-only Owner operations."""
from __future__ import annotations
import hmac
from typing import Mapping


ROLE_PERMISSIONS = {
    "owner": frozenset({"overview", "orders", "ledger", "reconciliation", "risk", "audit"}),
    "finance": frozenset({"overview", "ledger", "reconciliation"}),
    "compliance": frozenset({"overview", "orders", "ledger", "reconciliation", "risk", "audit"}),
}


class OwnerAuthorizationError(PermissionError):
    pass


def resolve_owner_role(authorization: str | None, tokens: Mapping[str, str]) -> str:
    supplied = (authorization or "").removeprefix("Bearer ").strip()
    if not supplied:
        raise OwnerAuthorizationError("Owner authorization required.")
    configured = {
        "owner": (tokens.get("owner", ""), tokens.get("legacy_admin", "")),
        "finance": (tokens.get("finance", ""),),
        "compliance": (tokens.get("compliance", ""),),
    }
    if not any(secret for values in configured.values() for secret in values):
        raise RuntimeError("Configure at least one Owner Console role token.")
    for role, secrets in configured.items():
        if any(secret and hmac.compare_digest(supplied, secret) for secret in secrets):
            return role
    raise OwnerAuthorizationError("Unauthorized.")


def require_owner_permission(authorization: str | None, permission: str,
                             tokens: Mapping[str, str]) -> str:
    role = resolve_owner_role(authorization, tokens)
    if permission not in ROLE_PERMISSIONS[role]:
        raise OwnerAuthorizationError("This role is not authorized for the requested Owner Console resource.")
    return role
