import pytest
import asyncio
import httpx
from workforce_oidc import WorkforceIdentityError
from workforce_code_exchange import validate_token_endpoint, exchange_authorization_code
from test_workforce_oidc import token_fixture


class FakeClient:
    def __init__(self, response):
        self.response = response
        self.calls = []

    async def post(self, url, *, data, auth):
        self.calls.append({"url": url, "data": data, "auth": auth})
        return self.response


def transaction():
    return {
        "verifier": "verifier-secret",
        "nonce": "expected-nonce",
        "client_id": "zencrypto-workforce",
        "redirect_uri": "https://admin.example.test/oidc/callback",
        "issuer": "https://id.example.test",
    }


def test_code_exchange_verifies_nonce_and_never_issues_session():
    token, config = token_fixture(claims_override={"nonce": "expected-nonce"})
    client = FakeClient(httpx.Response(200, json={"id_token": token}))
    result = asyncio.run(exchange_authorization_code(
        code="short-lived-code", transaction=transaction(),
        token_endpoint="https://id.example.test/token", oidc_config=config,
        client_secret="long-confidential-secret", http_client=client, now=1200,
    ))
    assert result["identity"]["cryptographic_identity_verified"] is True
    assert result["session_issued"] is False
    assert result["owner_access_granted"] is False
    assert result["mfa_verified"] is False
    assert client.calls[0]["data"]["code_verifier"] == "verifier-secret"
    assert client.calls[0]["auth"] == ("zencrypto-workforce", "long-confidential-secret")


def test_code_exchange_rejects_nonce_mismatch():
    token, config = token_fixture(claims_override={"nonce": "wrong-nonce"})
    with pytest.raises(WorkforceIdentityError):
        asyncio.run(exchange_authorization_code(
            code="short-lived-code", transaction=transaction(),
            token_endpoint="https://id.example.test/token", oidc_config=config,
            client_secret="long-confidential-secret",
            http_client=FakeClient(httpx.Response(200, json={"id_token": token})), now=1200,
        ))


@pytest.mark.parametrize("url", [
    "http://id.example.test/token",
    "https://evil.example.test/token",
    "https://id.example.test:444/token",
    "https://id.example.test/token?redirect=evil",
    "https://evil@id.example.test/token",
])
def test_token_endpoint_restrictions(url):
    with pytest.raises(WorkforceIdentityError):
        validate_token_endpoint(url, "https://id.example.test")


def test_code_exchange_rejects_invalid_client_and_missing_token():
    _, config = token_fixture()
    with pytest.raises(WorkforceIdentityError):
        asyncio.run(exchange_authorization_code(
            code="test", transaction=transaction(), token_endpoint="https://id.example.test/token",
            oidc_config=config, client_secret="short",
            http_client=FakeClient(httpx.Response(200, json={})),
        ))
    with pytest.raises(WorkforceIdentityError):
        asyncio.run(exchange_authorization_code(
            code="test", transaction=transaction(), token_endpoint="https://id.example.test/token",
            oidc_config=config, client_secret="long-confidential-secret",
            http_client=FakeClient(httpx.Response(200, json={})),
        ))
