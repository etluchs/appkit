import pytest


@pytest.fixture(autouse=True)
def fake_backend(monkeypatch):
    """Force the in-memory backend and reset it before every test."""
    monkeypatch.setenv("APPKIT_BACKEND", "fake")
    monkeypatch.delenv("CONTAINER_APP_NAME", raising=False)
    monkeypatch.delenv("WEBSITE_SITE_NAME", raising=False)
    import appkit

    appkit.reset_fakes()
    yield
    appkit.reset_fakes()


@pytest.fixture
def azure_backend(monkeypatch):
    """Switch to the azure backend with a stubbed managed-identity token.

    Everything below the credential runs for real: the same URL building,
    pagination, retry and error handling that production uses. Only the token
    *source* is replaced — that part belongs to azure-identity, not to appkit.

    Yields the list of backoff sleeps, so retry timing can be asserted on
    instead of waited for.
    """
    monkeypatch.setenv("APPKIT_BACKEND", "azure")
    monkeypatch.setattr("appkit._credential.token", lambda scope: "test-token")
    slept: list[float] = []
    monkeypatch.setattr("appkit._graph._sleep", slept.append)
    return slept


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
