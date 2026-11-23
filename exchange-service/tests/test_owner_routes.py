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

