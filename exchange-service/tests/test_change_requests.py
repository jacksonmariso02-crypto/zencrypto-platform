import pytest
from change_requests import ChangeRequestRegistry


def test_change_request_is_durable_and_never_executes(tmp_path):
    path = str(tmp_path / "changes.sqlite3")
    registry = ChangeRequestRegistry(path)
    created = registry.create(
        "configuration_change_review",
        "Review exchange order limit",
        "Review a proposed simulated order limit change before any implementation.",
    )
    assert created["execution_performed"] is False
    assert created["approval_granted"] is False
    assert created["status"] == "pending_review"
    second = ChangeRequestRegistry(path)
    report = second.list_requests()
    assert report["total"] == 1
    assert report["requests"][0]["request_id"] == created["request_id"]
    assert report["approvals_enabled"] is False
    assert report["execution_enabled"] is False


def test_change_request_rejects_invalid_type_and_payload(tmp_path):
    registry = ChangeRequestRegistry(str(tmp_path / "validation.sqlite3"))
    with pytest.raises(ValueError):
        registry.create("enable_live_trading", "Enable live", "Should not be supported.")
    with pytest.raises(ValueError):
        registry.create("staff_access_review", "short", "Valid rationale for request.")
    with pytest.raises(ValueError):
        registry.create("staff_access_review", "Review staff role", "too short")
    with pytest.raises(ValueError):
        registry.list_requests(limit=101)

def test_review_readiness_is_fail_closed_for_all_categories():
    from change_requests import REVIEW_EVIDENCE_REQUIREMENTS, review_readiness
    for category in REVIEW_EVIDENCE_REQUIREMENTS:
        result = review_readiness(category)
        assert len(result["required_evidence"]) >= 4
        assert result["verified_evidence_count"] == 0
        assert result["review_ready"] is False
        assert result["approval_granted"] is False
        assert result["execution_permitted"] is False
        assert len(result["blocking_reasons"]) >= 2
    with pytest.raises(ValueError):
        review_readiness("enable_live_trading")


def test_list_requests_includes_unverified_review_requirements(tmp_path):
    registry = ChangeRequestRegistry(str(tmp_path / "readiness.sqlite3"))
    registry.create("staff_access_review", "Review finance access", "Need an independent assessment of proposed finance access.")
    item = registry.list_requests()["requests"][0]
    assert item["review_readiness"]["review_ready"] is False
    assert item["review_readiness"]["execution_permitted"] is False
    assert "Independent approver distinct from requester" in item["review_readiness"]["required_evidence"]
