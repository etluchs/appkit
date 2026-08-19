"""Auth mode selection.

The Easy Auth headers are only meaningful when a trusted proxy put them there,
and appkit cannot detect that from inside the container. So it refuses to guess:
on an Azure app platform the mode must be stated, and the dev user — which would
otherwise sign every caller in with `APPKIT_DEV_ROLES` — is refused outright.
"""

import pytest

from appkit import ConfigError, auth


class Req:
    def __init__(self, headers):
        self.headers = headers


@pytest.fixture
def on_platform(monkeypatch):
    monkeypatch.setenv("CONTAINER_APP_NAME", "adate")


# --- mode selection -------------------------------------------------------

def test_defaults_follow_the_backend_off_platform(monkeypatch):
    monkeypatch.delenv("APPKIT_AUTH", raising=False)

    monkeypatch.setenv("APPKIT_BACKEND", "fake")
    assert auth.mode() == auth.DEV

    monkeypatch.setenv("APPKIT_BACKEND", "azure")
    assert auth.mode() == auth.EASYAUTH


def test_unrecognised_mode_is_an_error(monkeypatch):
    monkeypatch.setenv("APPKIT_AUTH", "trustme")

    with pytest.raises(ConfigError, match="not a valid auth mode"):
        auth.mode()


def test_mode_must_be_stated_on_an_azure_platform(monkeypatch, on_platform):
    monkeypatch.delenv("APPKIT_AUTH", raising=False)
    monkeypatch.setenv("APPKIT_BACKEND", "azure")

    with pytest.raises(ConfigError, match="APPKIT_AUTH is not set"):
        auth.mode()


def test_dev_mode_is_refused_on_an_azure_platform(monkeypatch, on_platform):
    """Otherwise a stray APPKIT_AUTH=dev signs everyone in as an approver."""
    monkeypatch.setenv("APPKIT_AUTH", "dev")

    with pytest.raises(ConfigError, match="refused on an Azure app platform"):
        auth.mode()


def test_easyauth_can_be_declared_on_platform(monkeypatch, on_platform):
    monkeypatch.setenv("APPKIT_AUTH", "easyauth")
    assert auth.mode() == auth.EASYAUTH


# --- what each mode does with a request -----------------------------------

def test_easyauth_trusts_the_headers_and_has_no_dev_fallback(monkeypatch):
    monkeypatch.setenv("APPKIT_AUTH", "easyauth")

    assert auth.user(Req({})) is None

    signed_in = auth.user(Req({"x-ms-client-principal-name": "ben.marti@uzh.ch"}))
    assert signed_in is not None
    assert signed_in.email == "ben.marti@uzh.ch"


def test_dev_mode_still_simulates_users_from_headers(monkeypatch, easy_auth_request):
    """Locally, headers are a convenient way to try out a role."""
    monkeypatch.setenv("APPKIT_AUTH", "dev")

    simulated = auth.user(easy_auth_request(roles=("approver",)))
    assert simulated.has_role("approver")

    assert auth.user(Req({})).provider == "dev"


def test_verify_mode_ignores_the_spoofable_headers(monkeypatch, easy_auth_request):
    """The whole point: in verify mode the principal headers count for nothing."""
    monkeypatch.setenv("APPKIT_AUTH", "verify")

    assert auth.user(easy_auth_request()) is None
    assert auth.user(Req({"x-ms-client-principal-name": "attacker@uzh.ch"})) is None
