"""Verify the Easy Auth id token (``APPKIT_AUTH=verify``).

Easy Auth forwards the raw id token it received from Entra ID in
``X-MS-TOKEN-AAD-ID-TOKEN`` when the token store is enabled. Unlike the
``X-MS-CLIENT-PRINCIPAL*`` headers, that token is **signed by the tenant**, so a
caller who can reach the container directly and invent headers cannot forge one.
Checking the signature is the only identity control appkit can apply from inside
the container that does not depend on trusting whatever is in front of it.

Requires the ``verify`` extra::

    uv pip install 'appkit[verify]'

and this configuration:

===========================  =================================================
``APPKIT_AUTH_TENANT_ID``    Entra tenant id (the ``tid`` claim) the token must
                             come from.
``APPKIT_AUTH_CLIENT_ID``    Application (client) id of the Easy Auth app
                             registration; the token's ``aud``.
``APPKIT_AUTH_AUTHORITY``    Login endpoint. Defaults to the public cloud,
                             ``https://login.microsoftonline.com``.
===========================  =================================================
"""

from __future__ import annotations

import threading
from typing import Any

from .config import env

PUBLIC_AUTHORITY = "https://login.microsoftonline.com"

_JWKS_CACHE_SECONDS = 600
_lock = threading.Lock()
_clients: dict[str, Any] = {}


def _authority() -> str:
    return (env("APPKIT_AUTH_AUTHORITY", PUBLIC_AUTHORITY) or PUBLIC_AUTHORITY).rstrip("/")


def issuer(tenant_id: str) -> str:
    return f"{_authority()}/{tenant_id}/v2.0"


def jwks_url(tenant_id: str) -> str:
    return f"{_authority()}/{tenant_id}/discovery/v2.0/keys"


def _jwks_client(tenant_id: str):
    """A cached PyJWKClient. It refetches signing keys as they rotate."""
    url = jwks_url(tenant_id)
    with _lock:
        client = _clients.get(url)
        if client is None:
            from jwt import PyJWKClient

            client = PyJWKClient(url, cache_keys=True, lifespan=_JWKS_CACHE_SECONDS)
            _clients[url] = client
        return client


def reset_cache() -> None:
    """Drop the cached JWKS clients (used by tests)."""
    with _lock:
        _clients.clear()


def verify_id_token(raw_token: str) -> dict[str, Any] | None:
    """Return the token's claims, or ``None`` if it is not valid.

    Invalid means anything PyJWT rejects: a bad or absent signature, an unknown
    signing key, the wrong issuer or audience, or an expired token. Every one of
    those is an unauthenticated request, not an error to show a user.
    """
    try:
        import jwt
    except ImportError as exc:  # pragma: no cover - depends on the extra
        raise RuntimeError(
            "APPKIT_AUTH=verify needs PyJWT. Install the extra: "
            "uv pip install 'appkit[verify]'"
        ) from exc

    tenant_id = env("APPKIT_AUTH_TENANT_ID", required=True)
    audience = env("APPKIT_AUTH_CLIENT_ID", required=True)

    try:
        signing_key = _jwks_client(tenant_id).get_signing_key_from_jwt(raw_token)
        return jwt.decode(
            raw_token,
            signing_key.key,
            algorithms=["RS256"],
            audience=audience,
            issuer=issuer(tenant_id),
            options={"require": ["exp", "aud", "iss"]},
        )
    except jwt.PyJWTError:
        # This covers the InvalidTokenError family and PyJWKClientError (an
        # unmatched or unfetchable signing key). All of them mean the same thing
        # to the app: not signed in. Anything else is a real bug and propagates.
        return None
