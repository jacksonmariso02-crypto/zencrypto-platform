import base64
import json
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa, padding
from cryptography.hazmat.primitives import hashes
from workforce_oidc import WorkforceOIDCConfig, WorkforceIdentityError, verify_workforce_token


def b64(value):
    if isinstance(value, dict):
        value = json.dumps(value, separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode()


def token_fixture(*, claims_override=None, header_override=None):
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public = private.public_key().public_numbers()
    jwks = {"keys": [{"kid": "test-1", "kty": "RSA", "use": "sig", "alg": "RS256",
                      "n": b64(public.n.to_bytes(256, "big")),
                      "e": b64(public.e.to_bytes(3, "big"))}]}
    config = WorkforceOIDCConfig("https://id.example.test", "zencrypto-workforce", json.dumps(jwks))
    header = {"alg": "RS256", "typ": "JWT", "kid": "test-1"}
    claims = {"iss": config.issuer, "aud": config.audience, "sub": "staff-001",
              "iat": 1000, "exp": 1600}
    header.update(header_override or {})
    claims.update(claims_override or {})
    message = b64(header) + "." + b64(claims)
    signature = private.sign(message.encode(), padding.PKCS1v15(), hashes.SHA256())
    return message + "." + b64(signature), config


def test_valid_workforce_identity_does_not_grant_owner_access():
    token, config = token_fixture()
    result = verify_workforce_token(token, config, now=1200)
    assert result["subject"] == "staff-001"
    assert result["cryptographic_identity_verified"] is True
    assert result["mfa_verified"] is False
    assert result["privileged_access_granted"] is False
    assert result["approval_granted"] is False


@pytest.mark.parametrize("override", [
    {"iss": "https://attacker.example"},
    {"aud": "other-client"},
    {"exp": 1199},
    {"iat": 1300, "exp": 1600},
    {"sub": ""},
    {"nbf": 1400},
    {"aud": ["zencrypto-workforce", "another-client"]},
])
def test_invalid_claims_are_rejected(override):
    token, config = token_fixture(claims_override=override)
    with pytest.raises(WorkforceIdentityError):
        verify_workforce_token(token, config, now=1200)


def test_algorithm_confusion_and_tampering_are_rejected():
    token, config = token_fixture(header_override={"alg": "none"})
    with pytest.raises(WorkforceIdentityError):
        verify_workforce_token(token, config, now=1200)
    token, config = token_fixture()
    with pytest.raises(WorkforceIdentityError):
        verify_workforce_token(token[:-5] + "AAAAA", config, now=1200)
    with pytest.raises(WorkforceIdentityError):
        verify_workforce_token(token, WorkforceOIDCConfig("", "", ""), now=1200)
