"""Contract tests for ``mail.send_mail`` on the azure backend."""

import httpx
import pytest
import respx

from appkit import ConfigError, GraphError, mail
from appkit._graph import GRAPH_BASE

SENDER = "app@uzh.ch"
SEND = f"{GRAPH_BASE}/users/{SENDER}/sendMail"


@pytest.fixture
def sender_env(monkeypatch):
    monkeypatch.setenv("APPKIT_MAIL_SENDER", SENDER)


@respx.mock
def test_posts_the_expected_message(azure_backend, sender_env):
    respx.post(SEND).mock(return_value=httpx.Response(202))

    mail.send_mail(to="team@uzh.ch", subject="Hello", body="Body text")

    request = respx.calls.last.request
    assert request.url.path == f"/v1.0/users/{SENDER}/sendMail"
    payload = _json(request)
    assert payload["saveToSentItems"] is True
    assert payload["message"]["subject"] == "Hello"
    assert payload["message"]["body"] == {"contentType": "Text", "content": "Body text"}
    assert payload["message"]["toRecipients"] == [
        {"emailAddress": {"address": "team@uzh.ch"}}
    ]
    assert "ccRecipients" not in payload["message"]


@respx.mock
def test_html_and_cc(azure_backend, sender_env):
    respx.post(SEND).mock(return_value=httpx.Response(202))

    mail.send_mail(
        to=["a@uzh.ch", "b@uzh.ch"],
        cc="boss@uzh.ch",
        subject="Report",
        body="<p>hi</p>",
        html=True,
    )

    message = _json(respx.calls.last.request)["message"]
    assert message["body"]["contentType"] == "HTML"
    assert [r["emailAddress"]["address"] for r in message["toRecipients"]] == [
        "a@uzh.ch",
        "b@uzh.ch",
    ]
    assert message["ccRecipients"] == [{"emailAddress": {"address": "boss@uzh.ch"}}]


@respx.mock
def test_sender_argument_overrides_the_environment(azure_backend, sender_env):
    other = "other@uzh.ch"
    respx.post(f"{GRAPH_BASE}/users/{other}/sendMail").mock(
        return_value=httpx.Response(202)
    )

    mail.send_mail(to="team@uzh.ch", subject="s", body="b", sender=other)

    assert respx.calls.last.request.url.path.endswith(f"/users/{other}/sendMail")


def test_missing_sender_configuration_fails_loudly(azure_backend, monkeypatch):
    monkeypatch.delenv("APPKIT_MAIL_SENDER", raising=False)

    with pytest.raises(ConfigError, match="APPKIT_MAIL_SENDER"):
        mail.send_mail(to="team@uzh.ch", subject="s", body="b")


def test_recipient_validation_happens_before_any_request(azure_backend, sender_env):
    # No respx mock registered: a request here would raise a connection error.
    with pytest.raises(ValueError):
        mail.send_mail(to=[], subject="s", body="b")


@respx.mock
def test_send_failure_surfaces_as_graph_error(azure_backend, sender_env):
    respx.post(SEND).mock(
        return_value=httpx.Response(
            403,
            json={
                "error": {
                    "code": "ErrorAccessDenied",
                    "message": "Access to OData is disabled.",
                }
            },
        )
    )

    with pytest.raises(GraphError) as exc:
        mail.send_mail(to="team@uzh.ch", subject="s", body="b")

    assert exc.value.code == "ErrorAccessDenied"


def test_divergence_outbox_is_silently_empty_in_azure_mode(azure_backend, sender_env):
    """``outbox()`` always reads the *fake* outbox.

    A test that asserts on it therefore passes in azure mode without proving
    anything was sent. Pinned so the foot-gun is documented.
    """
    assert mail.outbox() == []


def _json(request):
    import json

    return json.loads(request.content)
