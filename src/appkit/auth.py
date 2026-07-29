"""Identify the signed-in user from Azure Container Apps Easy Auth headers.

When a Container App has authentication enabled, the platform terminates the
login and injects headers on every request that reaches your container:

* ``X-MS-CLIENT-PRINCIPAL-NAME`` – the user's name / UPN
* ``X-MS-CLIENT-PRINCIPAL-ID``   – a stable id for the user
* ``X-MS-CLIENT-PRINCIPAL-IDP``  – the identity provider (e.g. ``aad``)
* ``X-MS-CLIENT-PRINCIPAL``       – base64-encoded JSON with the full claim set

:func:`user` reads those headers and returns a :class:`User`. It works with any
object exposing a case-insensitive ``.headers`` mapping – a Starlette/FastAPI
``Request`` is the typical caller, but a plain dict works too.

Locally (``fake`` backend, no headers present) it returns a configurable dev
user so pages that need "who am I" render without a login round-trip.
"""

from __future__ import annotations

import base64
import binascii
import json
from dataclasses import dataclass, field
from typing import Any, Protocol

from .config import env, is_fake

# Claim type URIs emitted by Azure AD via Easy Auth.
_NAME_CLAIMS = (
    "name",
    "preferred_username",
    "http://schemas.xmlsoap.org/ws/2005/05/identity/claims/name",
)
_EMAIL_CLAIMS = (
    "email",
    "preferred_username",
    "http://schemas.xmlsoap.org/ws/2005/05/identity/claims/emailaddress",
)
_ID_CLAIMS = (
    "http://schemas.microsoft.com/identity/claims/objectidentifier",
    "oid",
    "sub",
    "http://schemas.xmlsoap.org/ws/2005/05/identity/claims/nameidentifier",
)
_ROLE_CLAIMS = (
    "roles",
    "http://schemas.microsoft.com/ws/2008/06/identity/claims/role",
)


@dataclass(frozen=True)
class User:
    """A signed-in user."""

    id: str
    name: str
    email: str = ""
    provider: str = ""
    roles: tuple[str, ...] = field(default_factory=tuple)

    @property
    def is_authenticated(self) -> bool:
        return bool(self.id)

    def has_role(self, role: str) -> bool:
        return role in self.roles


class _HasHeaders(Protocol):
    headers: Any


def user(request: _HasHeaders) -> User | None:
    """Return the signed-in :class:`User`, or ``None`` if unauthenticated.

    Args:
        request: Anything with a ``.headers`` mapping (case-insensitive lookup
            is assumed, as with Starlette/FastAPI requests).
    """
    headers = _Headers(getattr(request, "headers", request))

    encoded = headers.get("x-ms-client-principal")
    if encoded:
        parsed = _from_principal(encoded, headers)
        if parsed is not None:
            return parsed

    name = headers.get("x-ms-client-principal-name")
    uid = headers.get("x-ms-client-principal-id")
    idp = headers.get("x-ms-client-principal-idp", "")
    if name or uid:
        return User(
            id=uid or name or "",
            name=name or uid or "",
            email=name if name and "@" in name else "",
            provider=idp or "",
        )

    # No Easy Auth headers. Locally, hand back a dev user; in Azure, nobody.
    if is_fake():
        return _dev_user()
    return None


def _from_principal(encoded: str, headers: _Headers) -> User | None:
    claims = _decode_principal(encoded)
    if claims is None:
        return None

    values = _claim_map(claims)
    roles = tuple(_claim_all(claims, _ROLE_CLAIMS))
    name = _first(values, _NAME_CLAIMS) or headers.get("x-ms-client-principal-name", "")
    return User(
        id=_first(values, _ID_CLAIMS)
        or headers.get("x-ms-client-principal-id", "")
        or name,
        name=name,
        email=_first(values, _EMAIL_CLAIMS) or (name if "@" in name else ""),
        provider=headers.get("x-ms-client-principal-idp", "")
        or str(claims.get("auth_typ", "")),
        roles=roles,
    )


def _decode_principal(encoded: str) -> dict[str, Any] | None:
    try:
        raw = base64.b64decode(encoded)
        data = json.loads(raw)
    except (binascii.Error, ValueError, UnicodeDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _claim_map(principal: dict[str, Any]) -> dict[str, list[str]]:
    """Collapse the ``claims`` array into ``{type: [values...]}``."""
    out: dict[str, list[str]] = {}
    for claim in principal.get("claims", []):
        typ = claim.get("typ")
        val = claim.get("val")
        if typ is None or val is None:
            continue
        out.setdefault(typ, []).append(val)
    return out


def _first(values: dict[str, list[str]], keys: tuple[str, ...]) -> str:
    for key in keys:
        if values.get(key):
            return values[key][0]
    return ""


def _claim_all(principal: dict[str, Any], keys: tuple[str, ...]) -> list[str]:
    values = _claim_map(principal)
    out: list[str] = []
    for key in keys:
        out.extend(values.get(key, []))
    return out


def _dev_user() -> User:
    name = env("APPKIT_DEV_USER", "Dev User") or "Dev User"
    email = env("APPKIT_DEV_EMAIL", "dev.user@uzh.ch") or ""
    roles = tuple(r for r in (env("APPKIT_DEV_ROLES", "") or "").split(",") if r)
    return User(id="dev-user", name=name, email=email, provider="dev", roles=roles)


class _Headers:
    """Case-insensitive header lookup over a mapping or Starlette Headers."""

    def __init__(self, source: Any) -> None:
        self._source = source

    def get(self, key: str, default: str = "") -> str:
        source = self._source
        # Starlette Headers already do case-insensitive lookup.
        try:
            value = source.get(key)
            if value is not None:
                return value
        except AttributeError:
            pass
        # Fall back to a plain dict with arbitrary casing.
        if isinstance(source, dict):
            lowered = {k.lower(): v for k, v in source.items()}
            return lowered.get(key.lower(), default)
        return default
