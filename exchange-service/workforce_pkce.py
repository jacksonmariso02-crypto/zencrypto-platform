"""OIDC Authorization Code + PKCE transaction primitives.

This module intentionally does not initiate network requests, exchange tokens,
create browser sessions, authenticate MFA, or grant administrator permissions.
An external vetted IdP integration must use the single-use transaction store.
"""
import base64
import hashlib
import hmac
import secrets
import threading
import time
from urllib.parse import urlencode, urlsplit

from workforce_oidc import WorkforceIdentityError


def _b64(value):
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _safe_https_url(value):
    try:
        parsed = urlsplit(value)
    except (TypeError, ValueError) as exc:
        raise WorkforceIdentityError("Invalid OIDC endpoint URL.") from exc
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.fragment:
        raise WorkforceIdentityError("Invalid OIDC endpoint URL.")
    if parsed.query or parsed.path.startswith("//"):
        raise WorkforceIdentityError("OIDC endpoint URL must not include query or fragment.")
    return value


class OIDCPKCETransactions:
    """Process-local, single-use PKCE state. Not a distributed session store."""

    def __init__(self, *, max_pending=500, ttl_seconds=300, clock=None):
        self.max_pending = max_pending
        self.ttl_seconds = ttl_seconds
        self.clock = clock or time.time
        self._pending = {}
        self._lock = threading.Lock()

    def begin(self, *, authorization_endpoint, client_id, redirect_uri, issuer):
        _safe_https_url(authorization_endpoint)
        _safe_https_url(redirect_uri)
        if not isinstance(client_id, str) or not 1 <= len(client_id) <= 256:
            raise WorkforceIdentityError("Invalid OIDC client identifier.")
        if not isinstance(issuer, str) or not issuer.startswith("https://"):
            raise WorkforceIdentityError("Invalid OIDC issuer.")
        if not 60 <= self.ttl_seconds <= 600 or not 1 <= self.max_pending <= 10000:
            raise WorkforceIdentityError("Invalid OIDC transaction configuration.")
        verifier = _b64(secrets.token_bytes(32))
        challenge = _b64(hashlib.sha256(verifier.encode("ascii")).digest())
        state = _b64(secrets.token_bytes(32))
        nonce = _b64(secrets.token_bytes(32))
        now = int(self.clock())
        with self._lock:
            self._pending = {k: v for k, v in self._pending.items() if v["expires_at"] > now}
            if len(self._pending) >= self.max_pending:
                raise WorkforceIdentityError("Too many pending OIDC transactions.")
            self._pending[hashlib.sha256(state.encode()).hexdigest()] = {
                "verifier": verifier, "nonce": nonce, "issuer": issuer,
                "client_id": client_id, "redirect_uri": redirect_uri,
                "expires_at": now + self.ttl_seconds,
            }
        url = authorization_endpoint + "?" + urlencode({
            "response_type": "code",
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "scope": "openid",
            "state": state,
            "nonce": nonce,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        })
        return {"authorization_url": url, "state": state, "expires_at": now + self.ttl_seconds}

    def consume(self, *, state, returned_issuer):
        """Consume exactly once; caller must separately exchange code and verify nonce."""
        if not isinstance(state, str) or len(state) != 43:
            raise WorkforceIdentityError("Invalid OIDC state.")
        key = hashlib.sha256(state.encode()).hexdigest()
        with self._lock:
            transaction = self._pending.pop(key, None)
        if transaction is None or transaction["expires_at"] <= int(self.clock()):
            raise WorkforceIdentityError("OIDC state expired, missing or already consumed.")
        if not isinstance(returned_issuer, str) or not hmac.compare_digest(transaction["issuer"], returned_issuer):
            raise WorkforceIdentityError("OIDC issuer mismatch.")
        return transaction
