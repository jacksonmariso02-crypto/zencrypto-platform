"""Conservative production identity / approval preflight. No approval is issued.

This is a static control inventory, not a live IdP or MFA attestation.
"""
from owner_access import ROLE_PERMISSIONS

CONTROL_CATALOG = (
    ("per_person_workforce_identity", "Workforce IdP with per-person accounts and revocation", "not_integrated"),
    ("phishing_resistant_mfa", "Phishing-resistant MFA enforced by the workforce IdP", "not_integrated"),
    ("independent_approver", "Reviewer principal must differ from requester", "not_integrated"),
    ("signed_approval_evidence", "Cryptographically signed decision evidence and independent retention", "not_integrated"),
    ("approval_expiration", "Time-bounded decisions with replay protection", "not_integrated"),
    ("isolated_execution_gate", "Execution service verifies policy and signed decision before any change", "not_integrated"),
    ("production_audit_retention", "Independent tamper-resistant audit retention", "not_integrated"),
    ("production_compliance_review", "Regulatory and operational readiness reviewed by qualified specialists", "not_verified"),
)


def identity_preflight(configured_role_tokens, *, sandbox, live_trading, routing, deposits, withdrawals):
    """Never treat token presence or feature flags as proof of identity or approval."""
    roles = {
        name: {
            "configured": bool(configured_role_tokens.get(name)),
            "permission_count": len(perms),
            "individual_identity_verified": False,
            "mfa_verified": False,
        }
        for name, perms in ROLE_PERMISSIONS.items()
    }
    controls = [
        {"id": key, "requirement": description, "status": state, "verified": False}
        for key, description, state in CONTROL_CATALOG
    ]
    unsafe_flags = [
        key for key, enabled in {
            "live_trading_enabled": live_trading,
            "customer_order_routing_enabled": routing,
            "deposits_enabled": deposits,
            "withdrawals_enabled": withdrawals,
        }.items() if enabled
    ]
    return {
        "mode": "PRODUCTION_IDENTITY_PREFLIGHT_ONLY",
        "environment": "sandbox" if sandbox else "non_sandbox",
        "roles": roles,
        "controls": controls,
        "verified_controls": 0,
        "required_controls": len(controls),
        "individual_approvers_supported": False,
        "dual_approval_supported": False,
        "execution_authorized": False,
        "production_ready": False,
        "unsafe_feature_flags": unsafe_flags,
        "warnings": [
            "Static configuration inventory; no IdP, MFA or human identity verification is performed.",
            "Role bearer tokens are shared role credentials, not unique approver identities.",
            "No approval, KYC decision, account change, configuration change or fund movement is authorized.",
        ],
    }
