"""Contract tests for the Microsoft Graph HTTP layer.

These drive the *real* ``_graph`` code path — the same URL building, retry
policy and error handling production uses — against a mocked transport. Only
the managed-identity token is stubbed (see the ``azure_backend`` fixture).

Everything asserted here was previously unexecuted by any test.
"""

import httpx
import pytest
import respx

from appkit import GraphError
from appkit._graph import GRAPH_BASE, get_all, post

ITEMS = f"{GRAPH_BASE}/sites/S/lists/L/items"
SEND = f"{GRAPH_BASE}/users/app@uzh.ch/sendMail"


def _error(status, code="somethingFailed", message="It failed."):
    return httpx.Response(
        status,
        json={"error": {"code": code, "message": message}},
        headers={"request-id": "req-42"},
    )


@respx.mock
def test_sends_bearer_token_and_accept_header(azure_backend):
    respx.get(ITEMS).mock(return_value=httpx.Response(200, json={"value": []}))

    get_all("/sites/S/lists/L/items")

    request = respx.calls.last.request
    assert request.headers["authorization"] == "Bearer test-token"
    assert request.headers["accept"] == "application/json"


@respx.mock
def test_get_all_follows_next_link(azure_backend):
    page2 = f"{ITEMS}?$skiptoken=abc"
    respx.get(ITEMS).mock(
        side_effect=[
            httpx.Response(200, json={"value": [{"id": "1"}], "@odata.nextLink": page2}),
            httpx.Response(200, json={"value": [{"id": "2"}]}),
        ]
    )

    items = get_all("/sites/S/lists/L/items", params={"$top": 1})

    assert [i["id"] for i in items] == ["1", "2"]
    # The nextLink already carries the query; appkit must not re-append params.
    assert respx.calls[1].request.url.params["$skiptoken"] == "abc"
    assert "$top" not in respx.calls[1].request.url.params


@respx.mock
def test_retries_throttling_and_honours_retry_after(azure_backend):
    slept = azure_backend
    respx.get(ITEMS).mock(
        side_effect=[
            httpx.Response(429, headers={"retry-after": "7"}),
            httpx.Response(200, json={"value": [{"id": "1"}]}),
        ]
    )

    assert len(get_all("/sites/S/lists/L/items")) == 1
    assert slept == [7.0]


@respx.mock
def test_retry_after_http_date_falls_back_to_backoff(azure_backend):
    slept = azure_backend
    respx.get(ITEMS).mock(
        side_effect=[
            httpx.Response(429, headers={"retry-after": "Wed, 21 Oct 2026 07:28:00 GMT"}),
            httpx.Response(200, json={"value": []}),
        ]
    )

    get_all("/sites/S/lists/L/items")
    assert slept and slept[0] > 0


@respx.mock
def test_gives_up_after_max_attempts(azure_backend):
    route = respx.get(ITEMS).mock(return_value=httpx.Response(429))

    with pytest.raises(GraphError) as exc:
        get_all("/sites/S/lists/L/items")

    assert route.call_count == 4
    assert exc.value.status == 429


@respx.mock
def test_read_retries_server_errors(azure_backend):
    respx.get(ITEMS).mock(
        side_effect=[
            httpx.Response(503),
            httpx.Response(500),
            httpx.Response(200, json={"value": []}),
        ]
    )

    assert get_all("/sites/S/lists/L/items") == []


@respx.mock
def test_post_is_not_retried_on_ambiguous_server_error(azure_backend):
    """sendMail is not idempotent: a 500 may mean the mail *was* sent."""
    route = respx.post(SEND).mock(return_value=_error(500, "internalError"))

    with pytest.raises(GraphError):
        post("/users/app@uzh.ch/sendMail", json={"message": {}})

    assert route.call_count == 1


@respx.mock
def test_post_is_retried_when_the_request_was_rejected(azure_backend):
    """429 and 503 mean Graph did not process the request, so a repeat is safe."""
    route = respx.post(SEND).mock(
        side_effect=[
            httpx.Response(429, headers={"retry-after": "1"}),
            httpx.Response(503),
            httpx.Response(202),
        ]
    )

    post("/users/app@uzh.ch/sendMail", json={"message": {}})
    assert route.call_count == 3


@respx.mock
def test_graph_error_carries_code_message_and_request_id(azure_backend):
    respx.get(ITEMS).mock(return_value=_error(403, "accessDenied", "Access denied."))

    with pytest.raises(GraphError) as exc:
        get_all("/sites/S/lists/L/items")

    error = exc.value
    assert (error.status, error.code, error.message) == (403, "accessDenied", "Access denied.")
    assert error.request_id == "req-42"
    # The message a non-engineer will see must name the likely cause.
    assert "permission" in str(error)
    assert "req-42" in str(error)


@respx.mock
def test_404_hint_explains_list_resolution(azure_backend):
    respx.get(ITEMS).mock(return_value=_error(404, "itemNotFound", "Not found."))

    with pytest.raises(GraphError) as exc:
        get_all("/sites/S/lists/L/items")

    assert "display name" in str(exc.value)


@respx.mock
def test_non_json_error_body_still_raises_graph_error(azure_backend):
    respx.get(ITEMS).mock(return_value=httpx.Response(502, text="<html>gateway</html>"))

    with pytest.raises(GraphError) as exc:
        get_all("/sites/S/lists/L/items")

    assert exc.value.status == 502
    assert "gateway" in exc.value.message


@respx.mock
def test_connect_errors_are_retried_then_surface(azure_backend):
    route = respx.get(ITEMS).mock(side_effect=httpx.ConnectError("no route to host"))

    with pytest.raises(httpx.ConnectError):
        get_all("/sites/S/lists/L/items")

    assert route.call_count == 4
