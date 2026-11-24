import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fastapi.testclient import TestClient
import main

client=TestClient(main.app)

def setup_roles(monkeypatch):
    monkeypatch.setattr(main,"OWNER_ROLE_TOKENS",{
        "owner":"owner-token-for-test-000000000000000000",
        "finance":"finance-token-for-test-00000000000000",
        "compliance":"compliance-token-for-test-000000000000",
        "legacy_admin":"legacy-token-for-test-0000000000000",
    })

def auth(token):
    return {"Authorization":"Bearer "+token}

def test_finance_can_read_overview_and_ledger_but_not_customer_orders(monkeypatch):
    setup_roles(monkeypatch)
    monkeypatch.setattr(main,"_readiness_checks",lambda:{})
    monkeypatch.setattr(main.OWNER_CONSOLE,"overview",lambda **kwargs:{"ok":True})
    monkeypatch.setattr(main.OWNER_CONSOLE,"ledger_integrity",lambda:{"healthy":True})
    monkeypatch.setattr(main.OWNER_CONSOLE,"recent_orders",lambda limit:{"orders":[]})
    assert client.get("/api/v1/owner/overview",headers=auth("finance-token-for-test-00000000000000")).status_code==200
    assert client.get("/api/v1/owner/ledger/integrity",headers=auth("finance-token-for-test-00000000000000")).status_code==200
    assert client.get("/api/v1/owner/orders",headers=auth("finance-token-for-test-00000000000000")).status_code==403

def test_compliance_can_read_orders(monkeypatch):
    setup_roles(monkeypatch)
    monkeypatch.setattr(main.OWNER_CONSOLE,"recent_orders",lambda limit:{"orders":[],"count":0})
    response=client.get("/api/v1/owner/orders",headers=auth("compliance-token-for-test-000000000000"))
    assert response.status_code==200

def test_legacy_admin_token_retains_owner_access(monkeypatch):
    setup_roles(monkeypatch)
    monkeypatch.setattr(main.OWNER_CONSOLE,"recent_orders",lambda limit:{"orders":[],"count":0})
    response=client.get("/api/v1/owner/orders",headers=auth("legacy-token-for-test-0000000000000"))
    assert response.status_code==200

def test_owner_routes_reject_missing_and_invalid_tokens(monkeypatch):
    setup_roles(monkeypatch)
    assert client.get("/api/v1/owner/overview").status_code==401
    assert client.get("/api/v1/owner/overview",headers=auth("wrong-token")).status_code==401

def test_finance_cannot_access_customer_order_details_but_owner_can(monkeypatch):
    setup_roles(monkeypatch)
    monkeypatch.setattr(main.OWNER_CONSOLE,"recent_orders",lambda limit:{"orders":[],"count":0})
    denied=client.get("/api/v1/owner/orders",headers=auth("finance-token-for-test-00000000000000"))
    allowed=client.get("/api/v1/owner/orders",headers=auth("owner-token-for-test-000000000000000000"))
    assert denied.status_code==403
    assert allowed.status_code==200


def test_finance_role_can_read_financial_summary(monkeypatch):
    setup_roles(monkeypatch)
    monkeypatch.setattr(main.OWNER_CONSOLE, "financial_summary", lambda days: {
        "period_days": days,
        "classification": "SIMULATED_PAPER_TRADING_ONLY_NOT_REAL_CUSTOMER_REVENUE_OR_CUSTODIED_ASSETS",
    })
    response = client.get(
        "/api/v1/owner/finance/summary?days=14",
        headers=auth("finance-token-for-test-00000000000000"),
    )
    assert response.status_code == 200
    assert response.json()["period_days"] == 14

def test_financial_summary_rejects_invalid_period_and_missing_auth(monkeypatch):
    setup_roles(monkeypatch)
    assert client.get("/api/v1/owner/finance/summary?days=0",
        headers=auth("owner-token-for-test-000000000000000000")).status_code == 400
    assert client.get("/api/v1/owner/finance/summary?days=30").status_code == 401


def test_risk_alerts_are_available_to_owner_and_compliance_only(monkeypatch):
    setup_roles(monkeypatch)
    monkeypatch.setattr(main, "_readiness_checks", lambda: {"database": True})
    monkeypatch.setattr(main.EXECUTION_RECONCILIATION, "reconciliation_report", lambda limit: {
        "mismatched_orders": 0, "unmatched_provider_orders": 0,
    })
    monkeypatch.setattr(main.OWNER_CONSOLE, "risk_alerts", lambda **kwargs: {
        "mode": "READ_ONLY_RISK_ALERTS", "alert_count": 0,
    })
    owner = client.get("/api/v1/owner/risk/alerts", headers=auth("owner-token-for-test-000000000000000000"))
    compliance = client.get("/api/v1/owner/risk/alerts", headers=auth("compliance-token-for-test-000000000000"))
    finance = client.get("/api/v1/owner/risk/alerts", headers=auth("finance-token-for-test-00000000000000"))
    assert owner.status_code == 200
    assert compliance.status_code == 200
    assert finance.status_code == 403



def test_audit_endpoints_are_available_to_owner_and_compliance_only(monkeypatch):
    setup_roles(monkeypatch)
    monkeypatch.setattr(main.AUDIT_LOG, "append", lambda **kwargs: {"sequence": 1})
    monkeypatch.setattr(main.AUDIT_LOG, "list_events", lambda **kwargs: {
        "mode": "READ_ONLY_APPEND_ONLY_AUDIT_LOG", "count": 0, "events": [],
    })
    monkeypatch.setattr(main.AUDIT_LOG, "verify_chain", lambda: {
        "healthy": True, "events_checked": 0, "issue_count": 0,
    })
    owner = client.get("/api/v1/owner/audit/events", headers=auth("owner-token-for-test-000000000000000000"))
    compliance = client.get("/api/v1/owner/audit/verify", headers=auth("compliance-token-for-test-000000000000"))
    finance = client.get("/api/v1/owner/audit/events", headers=auth("finance-token-for-test-00000000000000"))
    assert owner.status_code == 200
    assert compliance.status_code == 200
    assert finance.status_code == 403


def test_audit_event_endpoint_validates_pagination(monkeypatch):
    setup_roles(monkeypatch)
    monkeypatch.setattr(main.AUDIT_LOG, "append", lambda **kwargs: {"sequence": 1})
    assert client.get(
        "/api/v1/owner/audit/events?limit=0",
        headers=auth("owner-token-for-test-000000000000000000"),
    ).status_code == 400



def test_security_overview_is_owner_and_compliance_only(monkeypatch):
    setup_roles(monkeypatch)
    monkeypatch.setattr(main.AUDIT_LOG, "append", lambda **kwargs: {"sequence": 1})
    monkeypatch.setattr(main.AUDIT_LOG, "security_summary", lambda **kwargs: {
        "mode": "OBSERVED_SECURITY_TELEMETRY", "login_failures": 2,
        "login_successes": 4, "recent_events": [],
    })
    owner = client.get("/api/v1/owner/security/overview", headers=auth("owner-token-for-test-000000000000000000"))
    compliance = client.get("/api/v1/owner/security/overview", headers=auth("compliance-token-for-test-000000000000"))
    finance = client.get("/api/v1/owner/security/overview", headers=auth("finance-token-for-test-00000000000000"))
    assert owner.status_code == 200
    assert compliance.status_code == 200
    assert finance.status_code == 403


def test_security_overview_validates_window_and_limit(monkeypatch):
    setup_roles(monkeypatch)
    monkeypatch.setattr(main.AUDIT_LOG, "append", lambda **kwargs: {"sequence": 1})
    assert client.get(
        "/api/v1/owner/security/overview?hours=200",
        headers=auth("owner-token-for-test-000000000000000000"),
    ).status_code == 400



def test_master_control_center_is_owner_only_and_read_only(monkeypatch):
    setup_roles(monkeypatch)
    monkeypatch.setattr(main, "_readiness_checks", lambda: {"partner_selected": False})
    monkeypatch.setattr(main.AUDIT_LOG, "append", lambda **kwargs: {"sequence": 1})
    monkeypatch.setattr(main.AUDIT_LOG, "verify_chain", lambda: {
        "healthy": True, "status": "healthy", "events_checked": 3,
        "issue_count": 0, "issues": [], "head_hash": "a" * 64,
    })
    owner = client.get(
        "/api/v1/owner/control-center",
        headers=auth("owner-token-for-test-000000000000000000"),
    )
    assert owner.status_code == 200
    body = owner.json()
    assert body["mode"] == "OWNER_MASTER_CONTROL_READ_ONLY"
    assert body["write_actions_enabled"] is False
    assert body["available_control_actions"] == []
    assert body["platform_posture"]["customer_order_routing_enabled"] is False
    assert "role_permissions" in body
    assert not any("secret" in key.lower() or "token" in key.lower() for key in body)
    compliance = client.get(
        "/api/v1/owner/control-center",
        headers=auth("compliance-token-for-test-000000000000"),
    )
    finance = client.get(
        "/api/v1/owner/control-center",
        headers=auth("finance-token-for-test-00000000000000"),
    )
    assert compliance.status_code == 403
    assert finance.status_code == 403




def test_owner_administration_endpoints_are_owner_only_and_read_only(monkeypatch):
    setup_roles(monkeypatch)
    monkeypatch.setattr(main.AUDIT_LOG, "append", lambda **kwargs: {"sequence": 1})
    monkeypatch.setattr(main.AUTH_SERVICE, "list_users", lambda **kwargs: {
        "mode": "READ_ONLY_CUSTOMER_DIRECTORY", "total": 1, "limit": 50,
        "offset": 0, "users": [{"user_id": "u-1", "username": "trader_1",
        "account_id": "acct-1", "created_at": 123, "status": "active"}],
        "write_actions_enabled": False,
    })
    owner_headers = auth("owner-token-for-test-000000000000000000")
    users = client.get("/api/v1/owner/administration/users", headers=owner_headers)
    staff = client.get("/api/v1/owner/administration/staff-roles", headers=owner_headers)
    config = client.get("/api/v1/owner/administration/system-configuration", headers=owner_headers)
    assert users.status_code == staff.status_code == config.status_code == 200
    assert users.json()["write_actions_enabled"] is False
    assert "password_hash" not in str(users.json())
    assert "access_token" not in str(users.json())
    assert staff.json()["mode"] == "CONFIGURED_ROLE_INVENTORY"
    assert staff.json()["note"].startswith("This is not a staff roster")
    assert config.json()["secret_values_returned"] is False
    assert "AUTH_TOKEN_SECRET" not in str(config.json())
    assert client.get(
        "/api/v1/owner/administration/users",
        headers=auth("finance-token-for-test-00000000000000"),
    ).status_code == 403
    assert client.get(
        "/api/v1/owner/administration/staff-roles",
        headers=auth("compliance-token-for-test-000000000000"),
    ).status_code == 403


def test_owner_customer_directory_validates_filters(monkeypatch):
    setup_roles(monkeypatch)
    monkeypatch.setattr(main.AUDIT_LOG, "append", lambda **kwargs: {"sequence": 1})
    headers = auth("owner-token-for-test-000000000000000000")
    assert client.get("/api/v1/owner/administration/users?limit=101", headers=headers).status_code == 400
    assert client.get("/api/v1/owner/administration/users?status=suspended", headers=headers).status_code == 400
    assert client.get("/api/v1/owner/administration/users?offset=-1", headers=headers).status_code == 400



def test_owner_change_requests_create_and_list_without_execution(monkeypatch, tmp_path):
    from change_requests import ChangeRequestRegistry
    setup_roles(monkeypatch)
    monkeypatch.setattr(main, "CHANGE_REQUESTS", ChangeRequestRegistry(str(tmp_path / "requests.sqlite3")))
    monkeypatch.setattr(main.AUDIT_LOG, "append", lambda **kwargs: {"sequence": 1})
    owner = auth("owner-token-for-test-000000000000000000")
    payload = {
        "request_type": "customer_account_review",
        "summary": "Review customer account",
        "rationale": "Request an independent review before any customer account changes.",
    }
    created = client.post("/api/v1/owner/change-requests", headers=owner, json=payload)
    assert created.status_code == 201
    assert created.json()["approval_granted"] is False
    assert created.json()["execution_performed"] is False
    listed = client.get("/api/v1/owner/change-requests", headers=owner)
    assert listed.status_code == 200
    assert listed.json()["total"] == 1
    assert listed.json()["execution_enabled"] is False
    assert client.post(
        "/api/v1/owner/change-requests",
        headers=auth("compliance-token-for-test-000000000000"),
        json=payload,
    ).status_code == 403
    assert client.get(
        "/api/v1/owner/change-requests",
        headers=auth("finance-token-for-test-00000000000000"),
    ).status_code == 403
    assert client.post(
        "/api/v1/owner/change-requests", headers=owner,
        json={**payload, "request_type": "enable_live_trading"},
    ).status_code == 422


def test_review_policy_is_owner_only_and_fail_closed(monkeypatch):
    setup_roles(monkeypatch)
    monkeypatch.setattr(main.AUDIT_LOG, "append", lambda **kwargs: {"sequence": 1})
    owner = client.get("/api/v1/owner/review-policy", headers=auth("owner-token-for-test-000000000000000000"))
    assert owner.status_code == 200
    body = owner.json()
    assert body["execution_enabled"] is False
    assert body["independent_approval_available"] is False
    assert len(body["categories"]) == 5
    assert all(not entry["approval_granted"] for entry in body["categories"].values())
    assert client.get(
        "/api/v1/owner/review-policy",
        headers=auth("compliance-token-for-test-000000000000"),
    ).status_code == 403
    assert client.get(
        "/api/v1/owner/review-policy",
        headers=auth("finance-token-for-test-00000000000000"),
    ).status_code == 403

def test_owner_identity_preflight_is_owner_only_and_never_authorizes(monkeypatch):
    setup_roles(monkeypatch)
    monkeypatch.setattr(main.AUDIT_LOG, "append", lambda **kwargs: {"sequence": 1})
    owner = client.get("/api/v1/owner/identity-preflight", headers=auth("owner-token-for-test-000000000000000000"))
    assert owner.status_code == 200
    data = owner.json()
    assert data["production_ready"] is False
    assert data["execution_authorized"] is False
    assert data["dual_approval_supported"] is False
    assert data["verified_controls"] == 0
    for token in ("finance-token-for-test-00000000000000", "compliance-token-for-test-000000000000"):
        assert client.get("/api/v1/owner/identity-preflight", headers=auth(token)).status_code == 403

def test_workforce_identity_session_requires_configuration_and_never_grants_owner(monkeypatch):
    from workforce_oidc import WorkforceOIDCConfig
    monkeypatch.setattr(main.WorkforceOIDCConfig, "from_env", classmethod(lambda cls: WorkforceOIDCConfig("", "", "")))
    assert client.get("/api/v1/workforce/identity-session").status_code == 503
    monkeypatch.setattr(main.WorkforceOIDCConfig, "from_env", classmethod(lambda cls: WorkforceOIDCConfig("https://id.example.test", "aud", '{"keys":[]}')))
    assert client.get("/api/v1/workforce/identity-session").status_code == 401
    assert client.get("/api/v1/workforce/identity-session", headers={"Authorization": "Bearer not-a-jwt"}).status_code == 401
