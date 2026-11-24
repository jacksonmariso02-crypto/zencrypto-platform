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
