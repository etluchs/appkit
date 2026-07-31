import pytest


@pytest.fixture(autouse=True)
def fake_backend(monkeypatch):
    """Force the in-memory backend and reset it before every test."""
    monkeypatch.setenv("APPKIT_BACKEND", "fake")
    import appkit

    appkit.reset_fakes()
    yield
    appkit.reset_fakes()


@pytest.fixture
def easy_auth_request():
    """Build a fake request carrying Container Apps Easy Auth headers."""
    import base64
    import json

    class FakeRequest:
        def __init__(self, headers):
            self.headers = headers

    def make(*, name="Amelia Stucki", email="amelia.stucki@uzh.ch",
             oid="00000000-aaaa-bbbb-cccc-000000000001", roles=("approver",)):
        principal = {
            "auth_typ": "aad",
            "claims": [
                {"typ": "name", "val": name},
                {"typ": "preferred_username", "val": email},
                {"typ": "http://schemas.microsoft.com/identity/claims/objectidentifier",
                 "val": oid},
                *[{"typ": "roles", "val": r} for r in roles],
            ],
        }
        encoded = base64.b64encode(json.dumps(principal).encode()).decode()
        return FakeRequest({
            "x-ms-client-principal": encoded,
            "x-ms-client-principal-name": email,
            "x-ms-client-principal-id": oid,
            "x-ms-client-principal-idp": "aad",
        })

    return make
