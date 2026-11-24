"""Fail-closed OIDC authorization-code exchange primitives.

No HTTP routes or browser sessions use this yet. The token endpoint must be
operator-configured; never take a token endpoint or redirect from user input.
"""
import json
from urllib.parse import urlsplit

import httpx
from workforce_oidc import WorkforceIdentityError, verify_workforce_token
from workforce_pkce import _safe_https_url


def validate_token_endpoint(endpoint, issuer):
    _safe_https_url(endpoint)
    _safe_https_url(issuer)
    token_url = urlsplit(endpoint)
    issuer_url = urlsplit(issuer)
    if token_url.hostname != issuer_url.hostname or token_url.port != issuer_url.port:
        raise WorkforceIdentityError("Token endpoint origin must match the configured issuer.")
    return endpoint


async def exchange_authorization_code(
    *, code, transaction, token_endpoint, oidc_config, client_secret,
    http_client=None,
):
    """Back-channel confidential-client code exchange, returning identity inspection only."""
    if not isinstance(code, str) or not 1 <= len(code) <= 2048:
        raise WorkforceIdentityError("Invalid authorization code.")
    if not isinstance(transaction, dict) or not all(
        isinstance(transaction.get(key), str) and transaction[key]
        for key in ("verifier", "nonce", "client_id", "redirect_uri", "issuer")
    ):
        raise WorkforceIdentityError("Invalid authorization transaction.")
    if not isinstance(client_secret, str) or len(client_secret) < 16:
        raise WorkforceIdentityError("Confidential OIDC client secret is required.")
    if transaction["issuer"] != oidc_config.issuer:
        raise WorkforceIdentityError("Issuer mismatch.")
    validate_token_endpoint(token_endpoint, oidc_config.issuer)
    _safe_https_url(transaction["redirect_uri"])
    data = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": transaction["redirect_uri"],
        "client_id": transaction["client_id"],
        "code_verifier": transaction["verifier"],
    }
    auth = (transaction["client_id"], client_secret)
    try:
        if http_client is None:
            async with httpx.AsyncClient(timeout=8.0, follow_redirects=False) as client:
                response = await client.post(token_endpoint, data=data, auth=auth)
        else:
            response = await http_client.post(token_endpoint, data=data, auth=auth)
        response.raise_for_status()
        if len(response.content) > 65536:
            raise WorkforceIdentityError("Oversized OIDC token response.")
        payload = response.json()
    except (httpx.HTTPError, ValueError, json.JSONDecodeError) as exc:
        raise WorkforceIdentityError("OIDC code exchange failed.") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("id_token"), str):
        raise WorkforceIdentityError("OIDC token response missing ID token.")
    result = verify_workforce_token(payload["id_token"], oidc_config, required_nonce=transaction["nonce"])
    return {
        "identity": result,
        "session_issued": False,
        "owner_access_granted": False,
        "mfa_verified": False,
        "approval_granted": False,
        "note": "Back-channel code exchange and ID-token inspection only; no browser session or admin authorization.",
    }
