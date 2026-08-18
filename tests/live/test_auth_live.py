"""What the auth path can be checked headlessly.

Easy Auth itself cannot be exercised from a job: it needs a browser, a login and
an HTTP request through the platform's proxy. What *can* be checked without any
of that is whether ``APPKIT_AUTH=verify`` would work at all in this tenant —
that the discovery endpoint is reachable and publishes usable signing keys. A
verify-mode app that cannot fetch the JWKS signs nobody in.

The remaining piece — confirming Container Apps really forwards
``X-MS-TOKEN-AAD-ID-TOKEN``, and that the claim names match what appkit reads —
needs one browser login against a deployed app. See the README.
"""

import pytest

from appkit import auth
from appkit._jwt import jwks_url

pytestmark = pytest.mark.live


def test_the_auth_mode_is_one_a_deployment_should_use():
    mode = auth.mode()

    assert mode in (auth.EASYAUTH, auth.VERIFY)
    assert mode != auth.DEV, "a deployment must never run on the dev user"


def test_verify_mode_can_reach_its_signing_keys():
    if auth.mode() != auth.VERIFY:
        pytest.skip("APPKIT_AUTH is not `verify`")

    import httpx

    from appkit import config

    tenant = config.env("APPKIT_AUTH_TENANT_ID", required=True)
    response = httpx.get(jwks_url(tenant), timeout=15.0)
    response.raise_for_status()
    keys = response.json().get("keys", [])

    assert keys, "the tenant published no signing keys"
    assert any(key.get("kty") == "RSA" and key.get("kid") for key in keys)


def test_verify_mode_rejects_a_token_it_cannot_verify():
    """The control that matters: an invented token buys nothing."""
    if auth.mode() != auth.VERIFY:
        pytest.skip("APPKIT_AUTH is not `verify`")

    from appkit._jwt import verify_id_token

    assert verify_id_token("not-a-token") is None
