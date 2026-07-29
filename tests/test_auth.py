from appkit import auth


class Req:
    def __init__(self, headers):
        self.headers = headers


def test_user_from_full_principal(easy_auth_request):
    u = auth.user(easy_auth_request())
    assert u is not None
    assert u.name == "Amelia Stucki"
    assert u.email == "amelia.stucki@uzh.ch"
    assert u.id == "00000000-aaaa-bbbb-cccc-000000000001"
    assert u.provider == "aad"
    assert u.has_role("approver")
    assert u.is_authenticated


def test_user_from_simple_headers_without_principal_blob():
    u = auth.user(Req({
        "x-ms-client-principal-name": "ben.marti@uzh.ch",
        "x-ms-client-principal-id": "user-2",
        "x-ms-client-principal-idp": "aad",
    }))
    assert u is not None
    assert u.name == "ben.marti@uzh.ch"
    assert u.email == "ben.marti@uzh.ch"
    assert u.id == "user-2"


def test_user_falls_back_to_dev_user_locally():
    u = auth.user(Req({}))
    assert u is not None
    assert u.provider == "dev"
    assert u.is_authenticated


def test_dev_user_is_configurable(monkeypatch):
    monkeypatch.setenv("APPKIT_DEV_USER", "Tester")
    monkeypatch.setenv("APPKIT_DEV_EMAIL", "tester@uzh.ch")
    monkeypatch.setenv("APPKIT_DEV_ROLES", "admin,approver")
    u = auth.user(Req({}))
    assert u.name == "Tester"
    assert u.email == "tester@uzh.ch"
    assert u.roles == ("admin", "approver")


def test_no_user_in_azure_mode_without_headers(monkeypatch):
    monkeypatch.setenv("APPKIT_BACKEND", "azure")
    assert auth.user(Req({})) is None


def test_malformed_principal_falls_back_gracefully():
    u = auth.user(Req({
        "x-ms-client-principal": "not-valid-base64!!!",
        "x-ms-client-principal-name": "carla.rossi@uzh.ch",
        "x-ms-client-principal-id": "user-3",
    }))
    assert u is not None
    assert u.email == "carla.rossi@uzh.ch"


def test_dict_request_with_arbitrary_casing():
    u = auth.user({"X-MS-CLIENT-PRINCIPAL-NAME": "deniz@uzh.ch"})
    assert u is not None
    assert u.name == "deniz@uzh.ch"
