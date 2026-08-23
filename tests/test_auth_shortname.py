"""The on-premises account name that per-person authorization keys on.

Email is not an identity: shared mailboxes mean two people can carry the same
address. An app that authorizes on email grants one of them the other's
permissions, so appkit carries the shortname separately.
"""

import base64
import json

from appkit import auth


class FakeRequest:
    def __init__(self, headers):
        self.headers = headers


def principal(claims):
    blob = base64.b64encode(json.dumps({"auth_typ": "aad", "claims": claims}).encode()).decode()
    return FakeRequest({"x-ms-client-principal": blob})


def test_the_shortname_comes_from_the_easy_auth_claim(monkeypatch, easy_auth_request):
    monkeypatch.setenv("APPKIT_AUTH", "easyauth")

    user = auth.user(easy_auth_request(shortname="nhartma"))

    assert user.shortname == "nhartma"


def test_the_schema_uri_form_of_the_claim_is_understood(monkeypatch):
    monkeypatch.setenv("APPKIT_AUTH", "easyauth")
    request = principal(
        [
            {"typ": "name", "val": "Nicole Hartmann"},
            {
                "typ": "http://schemas.microsoft.com/identity/claims/onpremisessamaccountname",
                "val": "nhartma",
            },
        ]
    )

    assert auth.user(request).shortname == "nhartma"


def test_a_domain_qualified_account_name_is_reduced_to_the_account(monkeypatch):
    monkeypatch.setenv("APPKIT_AUTH", "easyauth")
    request = principal(
        [
            {"typ": "name", "val": "Nicole Hartmann"},
            {
                "typ": "http://schemas.xmlsoap.org/ws/2005/05/identity/claims/windowsaccountname",
                "val": "UZH\\NHartma",
            },
        ]
    )

    assert auth.user(request).shortname == "nhartma"


def test_a_tenant_that_emits_no_such_claim_yields_an_empty_shortname(monkeypatch):
    """Empty, never guessed — a wrong shortname is worse than a missing one."""
    monkeypatch.setenv("APPKIT_AUTH", "easyauth")
    request = principal(
        [
            {"typ": "name", "val": "Nicole Hartmann"},
            {"typ": "preferred_username", "val": "it-support@uzh.ch"},
        ]
    )

    assert auth.user(request).shortname == ""


def test_the_dev_user_has_a_configurable_shortname(monkeypatch):
    monkeypatch.setenv("APPKIT_AUTH", "dev")
    monkeypatch.setenv("APPKIT_DEV_SHORTNAME", "Amuster")

    assert auth.user(FakeRequest({})).shortname == "amuster"


def test_the_dev_user_has_a_shortname_by_default(monkeypatch):
    monkeypatch.setenv("APPKIT_AUTH", "dev")
    monkeypatch.delenv("APPKIT_DEV_SHORTNAME", raising=False)

    assert auth.user(FakeRequest({})).shortname == "devuser"
