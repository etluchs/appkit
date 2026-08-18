"""End-to-end verification of the Easy Auth id token (``APPKIT_AUTH=verify``).

These tests generate a real RSA key, sign real JWTs with it, and serve a real
JWKS document over a real local HTTP server — so the whole path runs: PyJWT
fetching the signing key by ``kid``, checking the RS256 signature, and enforcing
issuer, audience and expiry.

The attack this defends against is a caller who reaches the container without
passing through Easy Auth and invents the ``X-MS-CLIENT-PRINCIPAL`` headers.
Such a caller cannot produce a token signed by the tenant, which is what
``test_a_token_signed_by_the_wrong_key_is_rejected`` pins down.
"""

import base64
import json
import threading
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, HTTPServer

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from appkit import _jwt, auth

TENANT = "11111111-2222-3333-4444-555555555555"
CLIENT_ID = "99999999-8888-7777-6666-555555555555"
KID = "test-signing-key"


def _b64(value: int) -> str:
    raw = value.to_bytes((value.bit_length() + 7) // 8, "big")
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _jwks(*keys) -> dict:
    entries = []
    for kid, key in keys:
        numbers = key.public_key().public_numbers()
        entries.append(
            {
                "kty": "RSA",
                "use": "sig",
                "alg": "RS256",
                "kid": kid,
                "n": _b64(numbers.n),
                "e": _b64(numbers.e),
            }
        )
    return {"keys": entries}


@pytest.fixture(scope="module")
def signing_key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


@pytest.fixture(scope="module")
def other_key():
    """A key the tenant does not know about — i.e. an attacker's key."""
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


@pytest.fixture(scope="module")
def authority(signing_key):
    """Serve a JWKS document at the tenant's discovery URL."""
    document = json.dumps(_jwks((KID, signing_key))).encode()
    expected_path = f"/{TENANT}/discovery/v2.0/keys"

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802 - http.server's interface
            if self.path != expected_path:
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(document)))
            self.end_headers()
            self.wfile.write(document)

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()


@pytest.fixture
def verify_mode(monkeypatch, authority):
    monkeypatch.setenv("APPKIT_AUTH", "verify")
    monkeypatch.setenv("APPKIT_AUTH_TENANT_ID", TENANT)
    monkeypatch.setenv("APPKIT_AUTH_CLIENT_ID", CLIENT_ID)
    monkeypatch.setenv("APPKIT_AUTH_AUTHORITY", authority)
    _jwt.reset_cache()
    yield
    _jwt.reset_cache()


def _token(key, *, kid=KID, audience=CLIENT_ID, issuer=None, expires_in=3600, **claims):
    now = datetime.now(tz=UTC)
    payload = {
        "aud": audience,
        "iss": issuer if issuer is not None else f"{{authority}}/{TENANT}/v2.0",
        "iat": now,
        "exp": now + timedelta(seconds=expires_in),
        "oid": "00000000-aaaa-bbbb-cccc-000000000001",
        "name": "Amelia Stucki",
        "preferred_username": "amelia.stucki@uzh.ch",
        **claims,
    }
    return jwt.encode(payload, key, algorithm="RS256", headers={"kid": kid}), payload


def _request(token):
    class Req:
        headers = {"x-ms-token-aad-id-token": token}

    return Req()


def _issued(key, authority, **kwargs):
    kwargs.setdefault("issuer", f"{authority}/{TENANT}/v2.0")
    token, _ = _token(key, **kwargs)
    return _request(token)


# --- the happy path -------------------------------------------------------

def test_a_properly_signed_token_identifies_the_user(verify_mode, signing_key, authority):
    signed_in = auth.user(_issued(signing_key, authority, roles=["approver", "admin"]))

    assert signed_in is not None
    assert signed_in.id == "00000000-aaaa-bbbb-cccc-000000000001"
    assert signed_in.name == "Amelia Stucki"
    assert signed_in.email == "amelia.stucki@uzh.ch"
    assert signed_in.provider == "aad"
    assert signed_in.roles == ("approver", "admin")
    assert signed_in.has_role("approver")


def test_a_user_without_app_roles_has_none(verify_mode, signing_key, authority):
    signed_in = auth.user(_issued(signing_key, authority))

    assert signed_in.roles == ()
    assert not signed_in.has_role("approver")
    assert signed_in.is_authenticated


# --- the rejections -------------------------------------------------------

def test_a_token_signed_by_the_wrong_key_is_rejected(verify_mode, other_key, authority):
    """An attacker can mint any claims they like; they cannot sign them."""
    forged = _issued(other_key, authority, name="Attacker", roles=["approver"])

    assert auth.user(forged) is None


def test_an_unsigned_token_is_rejected(verify_mode, authority):
    payload = {"aud": CLIENT_ID, "iss": f"{authority}/{TENANT}/v2.0", "roles": ["approver"]}
    unsigned = jwt.encode(payload, key=None, algorithm="none")

    assert auth.user(_request(unsigned)) is None


def test_a_tampered_payload_is_rejected(verify_mode, signing_key, authority):
    token, payload = _token(signing_key, issuer=f"{authority}/{TENANT}/v2.0")
    header, _, signature = token.split(".")
    escalated = {**payload, "roles": ["approver"], "iat": 0, "exp": 9999999999}
    swapped = base64.urlsafe_b64encode(json.dumps(escalated).encode()).decode().rstrip("=")

    assert auth.user(_request(f"{header}.{swapped}.{signature}")) is None


def test_a_token_for_another_application_is_rejected(verify_mode, signing_key, authority):
    other_app = _issued(signing_key, authority, audience="some-other-app")

    assert auth.user(other_app) is None


def test_a_token_from_another_tenant_is_rejected(verify_mode, signing_key, authority):
    other_tenant = _issued(signing_key, authority, issuer=f"{authority}/other-tenant/v2.0")

    assert auth.user(other_tenant) is None


def test_an_expired_token_is_rejected(verify_mode, signing_key, authority):
    stale = _issued(signing_key, authority, expires_in=-60)

    assert auth.user(stale) is None


def test_an_unknown_signing_key_id_is_rejected(verify_mode, signing_key, authority):
    wrong_kid = _issued(signing_key, authority, kid="not-a-key-the-tenant-published")

    assert auth.user(wrong_kid) is None


def test_garbage_in_the_header_is_rejected(verify_mode):
    assert auth.user(_request("not-a-jwt")) is None


def test_no_token_means_nobody(verify_mode):
    class Req:
        headers = {}

    assert auth.user(Req()) is None


# --- configuration --------------------------------------------------------

def test_missing_tenant_configuration_fails_loudly(verify_mode, monkeypatch, signing_key,
                                                   authority):
    monkeypatch.delenv("APPKIT_AUTH_TENANT_ID", raising=False)

    from appkit import ConfigError

    with pytest.raises(ConfigError, match="APPKIT_AUTH_TENANT_ID"):
        auth.user(_issued(signing_key, authority))
