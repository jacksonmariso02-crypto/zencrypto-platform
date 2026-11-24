"""Optional workforce OIDC RS256 JWT verification, independent of legacy Owner tokens.

No authorization or approvals are granted by this module. JWKS is provided by
the operator's secret/configuration system; no dynamic URL fetch or token-derived
key lookup occurs. A verified identity is only a session-inspection result.
"""
import base64
import json
import hmac
import os
import time
from dataclasses import dataclass
from cryptography.hazmat.primitives.asymmetric import rsa, padding
from cryptography.hazmat.primitives import hashes


class WorkforceIdentityError(ValueError):
    pass


def _decode_b64url(value):
    if not isinstance(value, str) or len(value) > 16384:
        raise WorkforceIdentityError("Malformed identity token.")
    try:
        return base64.b64decode(value + "=" * (-len(value) % 4), altchars=b"-_", validate=True)
    except (ValueError, base64.binascii.Error) as exc:
        raise WorkforceIdentityError("Malformed identity token.") from exc


def _json_part(part):
    try:
        result = json.loads(_decode_b64url(part))
    except (UnicodeDecodeError, ValueError, TypeError) as exc:
        raise WorkforceIdentityError("Malformed identity token.") from exc
    if not isinstance(result, dict):
        raise WorkforceIdentityError("Malformed identity token.")
    return result


@dataclass(frozen=True)
class WorkforceOIDCConfig:
    issuer: str
    audience: str
    jwks_json: str

    @classmethod
    def from_env(cls):
        return cls(
            os.getenv("ZENCRYPTO_WORKFORCE_OIDC_ISSUER", "").strip(),
            os.getenv("ZENCRYPTO_WORKFORCE_OIDC_AUDIENCE", "").strip(),
            os.getenv("ZENCRYPTO_WORKFORCE_OIDC_JWKS_JSON", "").strip(),
        )

    @property
    def configured(self):
        return bool(self.issuer and self.audience and self.jwks_json)


def verify_workforce_token(token, config, *, now=None, required_nonce=None):
    """Verify RS256 identity and expiry; does not authorize privileged operations."""
    if not config.configured:
        raise WorkforceIdentityError("Workforce identity verification is not configured.")
    if not config.issuer.startswith("https://") or not config.audience:
        raise WorkforceIdentityError("Invalid workforce identity configuration.")
    if not isinstance(token, str) or len(token) > 16384:
        raise WorkforceIdentityError("Malformed identity token.")
    parts = token.split(".")
    if len(parts) != 3:
        raise WorkforceIdentityError("Malformed identity token.")
    header, claims = _json_part(parts[0]), _json_part(parts[1])
    if header.get("alg") != "RS256" or header.get("typ") not in (None, "JWT"):
        raise WorkforceIdentityError("Unsupported identity token algorithm.")
    kid = header.get("kid")
    if not isinstance(kid, str) or not 1 <= len(kid) <= 256:
        raise WorkforceIdentityError("Missing key identifier.")
    try:
        jwks = json.loads(config.jwks_json)
    except (ValueError, TypeError) as exc:
        raise WorkforceIdentityError("Invalid workforce JWKS configuration.") from exc
    keys = jwks.get("keys") if isinstance(jwks, dict) else None
    if not isinstance(keys, list) or len(keys) > 32:
        raise WorkforceIdentityError("Invalid workforce JWKS configuration.")
    candidates = [key for key in keys if isinstance(key, dict) and key.get("kid") == kid and key.get("kty") == "RSA" and key.get("use", "sig") == "sig" and key.get("alg", "RS256") == "RS256"]
    if len(candidates) != 1:
        raise WorkforceIdentityError("Untrusted identity signing key.")
    key = candidates[0]
    try:
        n = int.from_bytes(_decode_b64url(key["n"]), "big")
        e = int.from_bytes(_decode_b64url(key["e"]), "big")
        if n.bit_length() < 2048 or not 3 <= e <= 65537 or e % 2 == 0:
            raise WorkforceIdentityError("Invalid signing key strength.")
        public_key = rsa.RSAPublicNumbers(e, n).public_key()
        public_key.verify(
            _decode_b64url(parts[2]),
            (parts[0] + "." + parts[1]).encode("ascii"),
            padding.PKCS1v15(), hashes.SHA256(),
        )
    except (ValueError, KeyError, TypeError, OverflowError, UnicodeError) as exc:
        raise WorkforceIdentityError("Invalid identity signature or key.") from exc
    except Exception as exc:
        raise WorkforceIdentityError("Invalid identity signature or key.") from exc
    current = int(time.time() if now is None else now)
    audience = claims.get("aud")
    if claims.get("iss") != config.issuer or not (audience == config.audience or isinstance(audience, list) and config.audience in audience):
        raise WorkforceIdentityError("Invalid identity issuer or audience.")
    if isinstance(audience, list) and (not audience or not all(isinstance(x, str) for x in audience)):
        raise WorkforceIdentityError("Invalid identity audience.")
    if isinstance(audience, list) and len(audience) > 1 and claims.get("azp") != config.audience:
        raise WorkforceIdentityError("Unverified authorized party.")
    if required_nonce is not None:\n        if not isinstance(required_nonce, str) or not isinstance(claims.get("nonce"), str) or not hmac.compare_digest(claims["nonce"], required_nonce):\n            raise WorkforceIdentityError("Invalid OIDC nonce.")\n    sub = claims.get("sub")
    if not isinstance(sub, str) or not 1 <= len(sub) <= 256:
        raise WorkforceIdentityError("Invalid workforce subject.")
    exp, iat = claims.get("exp"), claims.get("iat")
    if type(exp) is not int or type(iat) is not int or exp <= current or iat > current + 60 or exp <= iat or exp - iat > 3600:
        raise WorkforceIdentityError("Invalid identity token lifetime.")
    nbf = claims.get("nbf")
    if nbf is not None and (type(nbf) is not int or nbf > current + 60):
        raise WorkforceIdentityError("Identity token not yet valid.")
    return {
        "subject": sub,
        "issuer": config.issuer,
        "audience": config.audience,
        "expires_at": exp,
        "cryptographic_identity_verified": True,
        "mfa_verified": False,
        "privileged_access_granted": False,
        "approval_granted": False,
        "note": "Signature and core claims verified. MFA, per-person workforce enrollment, authorization and approvals are not attested.",
    }
