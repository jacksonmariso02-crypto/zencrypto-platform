from urllib.parse import parse_qs, urlsplit

import pytest
from workforce_oidc import WorkforceIdentityError
from workforce_pkce import OIDCPKCETransactions


def begin(registry):
    return registry.begin(
        authorization_endpoint="https://id.example.test/authorize",
        client_id="zencrypto-workforce",
        redirect_uri="https://admin.example.test/oidc/callback",
        issuer="https://id.example.test",
    )


def test_pkce_challenge_nonce_and_single_use():
    registry = OIDCPKCETransactions(clock=lambda: 1000)
    request = begin(registry)
    query = parse_qs(urlsplit(request["authorization_url"]).query)
    assert query["response_type"] == ["code"]
    assert query["code_challenge_method"] == ["S256"]
    assert query["scope"] == ["openid"]
    assert len(query["nonce"][0]) == 43
    assert len(query["code_challenge"][0]) == 43
    assert "code_verifier" not in query
    transaction = registry.consume(state=request["state"], returned_issuer="https://id.example.test")
    assert transaction["nonce"] == query["nonce"][0]
    assert transaction["verifier"] != query["code_challenge"][0]
    with pytest.raises(WorkforceIdentityError):
        registry.consume(state=request["state"], returned_issuer="https://id.example.test")


def test_pkce_expiry_and_issuer_mismatch_fail_closed():
    now = [1000]
    registry = OIDCPKCETransactions(clock=lambda: now[0], ttl_seconds=60)
    request = begin(registry)
    with pytest.raises(WorkforceIdentityError):
        registry.consume(state=request["state"], returned_issuer="https://wrong.example.test")
    with pytest.raises(WorkforceIdentityError):
        registry.consume(state=request["state"], returned_issuer="https://id.example.test")
    request = begin(registry)
    now[0] += 61
    with pytest.raises(WorkforceIdentityError):
        registry.consume(state=request["state"], returned_issuer="https://id.example.test")


def test_pkce_capacity_and_url_validation():
    registry = OIDCPKCETransactions(max_pending=1, clock=lambda: 1000)
    begin(registry)
    with pytest.raises(WorkforceIdentityError):
        begin(registry)
    for bad in ("http://id.example.test/authorize", "https://evil@example.test/authorize", "https://id.example.test/authorize?redirect=evil"):
        with pytest.raises(WorkforceIdentityError):
            OIDCPKCETransactions().begin(
                authorization_endpoint=bad,
                client_id="workforce",
                redirect_uri="https://admin.example.test/callback",
                issuer="https://id.example.test",
            )
