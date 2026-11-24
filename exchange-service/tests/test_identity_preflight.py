from identity_preflight import CONTROL_CATALOG, identity_preflight


def test_identity_preflight_fails_closed_even_with_all_role_tokens():
    result = identity_preflight(
        {"owner": "configured", "finance": "configured", "compliance": "configured"},
        sandbox=True, live_trading=False, routing=False, deposits=False, withdrawals=False,
    )
    assert result["production_ready"] is False
    assert result["execution_authorized"] is False
    assert result["dual_approval_supported"] is False
    assert result["verified_controls"] == 0
    assert result["required_controls"] == len(CONTROL_CATALOG)
    assert all(not role["individual_identity_verified"] for role in result["roles"].values())
    assert all(not role["mfa_verified"] for role in result["roles"].values())
    assert all(not control["verified"] for control in result["controls"])
    assert "configured" not in str(result["warnings"])


def test_identity_preflight_detects_unsafe_flags_without_authorizing():
    result = identity_preflight(
        {}, sandbox=False, live_trading=True, routing=True, deposits=True, withdrawals=True,
    )
    assert len(result["unsafe_feature_flags"]) == 4
    assert result["execution_authorized"] is False
    assert result["production_ready"] is False
