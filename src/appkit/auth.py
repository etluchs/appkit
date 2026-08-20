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

Trusting those headers
----------------------

**Those headers are only meaningful if a trusted proxy put them there.** Easy
Auth strips client-supplied ``X-MS-CLIENT-PRINCIPAL*`` headers from inbound
requests and injects its own, so behind it they are trustworthy. Any request
path that does *not* pass through it — internal ingress, another container in
the same environment, a port exposed directly, or Easy Auth left in "allow
unauthenticated" mode — lets the caller set them by hand, and with them
``has_role()``.

appkit cannot detect that from inside the container, so it will not guess.
``APPKIT_AUTH`` states how the user is established:

* ``easyauth`` – trust the platform-injected headers. Requires that Easy Auth
  is enabled and configured to **reject** unauthenticated requests.
* ``verify``   – ignore those headers and cryptographically verify the
  ``X-MS-TOKEN-AAD-ID-TOKEN`` JWT against the tenant's signing keys. Forged
  headers cannot survive this. Needs Easy Auth's token store enabled and the
  ``appkit[verify]`` extra installed.
* ``public``   – nobody is signed in. The platform headers are ignored
  entirely rather than trusted, so :func:`user` always returns ``None`` and a
  forged header buys the caller nothing. For apps that are deliberately
  anonymous, which is a different statement from ``easyauth`` with no Easy
  Auth in front of it.
* ``dev``      – the local dev user (and header simulation). Refused outright
  when running on an Azure app platform.

Unset, ``APPKIT_AUTH`` follows the backend (``fake`` → ``dev``, ``azure`` →
``easyauth``); on Container Apps or App Service it must be set explicitly, so
that trusting a proxy is always a decision somebody made on purpose.
"""

from __future__ import annotations

import base64
import binascii
import json
import os
from dataclasses import dataclass, field
from typing import Any, Protocol

from .config import env, is_fake, on_azure_platform
from .errors import ConfigError

EASYAUTH = "easyauth"
VERIFY = "verify"
PUBLIC = "public"
DEV = "dev"
_MODES = (EASYAUTH, VERIFY, PUBLIC, DEV)

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


def mode() -> str:
    """Return the active auth mode (``easyauth``, ``verify``, ``public`` or ``dev``).

    Raises:
        ConfigError: if ``APPKIT_AUTH`` is unrecognised, is unset while running
            on an Azure app platform, or asks for the dev user there.
    """
    raw = os.getenv("APPKIT_AUTH")
    value = (raw or "").strip().lower()

    if value and value not in _MODES:
        raise ConfigError(
            f"APPKIT_AUTH={raw!r} is not a valid auth mode. "
            f"Use one of: {', '.join(_MODES)}."
        )

    if not value:
        if on_azure_platform():
            raise ConfigError(
                "APPKIT_AUTH is not set, but this app is running on an Azure app "
                "platform. Set APPKIT_AUTH=easyauth to trust the Easy Auth "
                "headers — which is only safe if Easy Auth is enabled and set to "
                "reject unauthenticated requests, so that no request can reach "
                "this container carrying headers a caller chose — or "
                "APPKIT_AUTH=verify to validate the signed id token instead. "
                "If the app has no sign-in at all, say so with "
                "APPKIT_AUTH=public, which ignores those headers rather than "
                "trusting them."
            )
        value = DEV if is_fake() else EASYAUTH

    if value == DEV and on_azure_platform():
        raise ConfigError(
            "APPKIT_AUTH=dev is refused on an Azure app platform: it would sign "
            "every caller in as the dev user, with the roles named by "
            "APPKIT_DEV_ROLES. Use easyauth or verify, or public if the app "
            "has no sign-in at all."
        )

    return value


def user(request: _HasHeaders) -> User | None:
    """Return the signed-in :class:`User`, or ``None`` if unauthenticated.

    Args:
        request: Anything with a ``.headers`` mapping (case-insensitive lookup
            is assumed, as with Starlette/FastAPI requests).
    """
    active = mode()

    # Before the headers are so much as read: a public app establishes no user,
    # so forging X-MS-CLIENT-PRINCIPAL gains a caller nothing. Falling through
    # to _from_headers here — as easyauth does — would hand any caller the
    # roles they asked for, which is the whole thing this mode rules out.
    if active == PUBLIC:
        return None

    headers = _Headers(getattr(request, "headers", request))

    if active == VERIFY:
        return _verified_user(headers)

    from_headers = _from_headers(headers)
    if from_headers is not None:
        return from_headers

    # Locally, hand back a dev user so pages render without a login.
    if active == DEV:
        return _dev_user()
    return None


def _from_headers(headers: _Headers) -> User | None:
    """Build a user from the Easy Auth headers, trusting them as given."""
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
    return None


def _verified_user(headers: _Headers) -> User | None:
    """Build a user from the id token, and only if its signature checks out."""
    from ._jwt import verify_id_token

    raw = headers.get("x-ms-token-aad-id-token")
    if not raw:
        return None

    claims = verify_id_token(raw)
    if claims is None:
        return None

    name = str(claims.get("name") or claims.get("preferred_username") or "")
    email = str(claims.get("preferred_username") or claims.get("email") or "")
    roles = claims.get("roles") or []
    return User(
        id=str(claims.get("oid") or claims.get("sub") or ""),
        name=name,
        email=email if "@" in email else "",
        provider="aad",
        roles=tuple(str(r) for r in roles),
    )


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
