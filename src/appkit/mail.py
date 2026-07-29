"""Send mail via the Microsoft Graph ``sendMail`` endpoint.

Public surface::

    from appkit import mail
    mail.send_mail(to="team@uzh.ch", subject="Daily summary", body="...")

In ``fake`` mode nothing leaves the process – messages are appended to an
in-memory outbox you can inspect in tests with :func:`outbox`. In ``azure``
mode the message is sent as the configured sender using the app's managed
identity.
"""

from __future__ import annotations

from collections.abc import Iterable

from .config import env, is_fake


def send_mail(
    *,
    to: str | Iterable[str],
    subject: str,
    body: str,
    html: bool = False,
    cc: str | Iterable[str] | None = None,
    sender: str | None = None,
) -> None:
    """Send an email.

    Args:
        to: One recipient address or an iterable of them.
        subject: Message subject.
        body: Message body (plain text unless ``html=True``).
        html: Treat ``body`` as HTML.
        cc: Optional CC recipient(s).
        sender: The mailbox to send as. Defaults to ``APPKIT_MAIL_SENDER``.
            Ignored by the fake backend.
    """
    to_list = _as_list(to)
    cc_list = _as_list(cc)
    if not to_list:
        raise ValueError("send_mail requires at least one recipient in `to`")

    if is_fake():
        from . import _fake

        _fake.record_mail(
            {
                "to": to_list,
                "cc": cc_list,
                "subject": subject,
                "body": body,
                "html": html,
            }
        )
        return

    sender = sender or env("APPKIT_MAIL_SENDER", required=True)
    _send_via_graph(
        sender=sender,
        to_list=to_list,
        cc_list=cc_list,
        subject=subject,
        body=body,
        html=html,
    )


def outbox() -> list[dict]:
    """Return a copy of the fake outbox (fake backend only)."""
    from . import _fake

    return _fake.outbox()


def _as_list(value: str | Iterable[str] | None) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    return [str(v) for v in value]


def _recipients(addresses: list[str]) -> list[dict]:
    return [{"emailAddress": {"address": address}} for address in addresses]


def _send_via_graph(
    *,
    sender: str,
    to_list: list[str],
    cc_list: list[str],
    subject: str,
    body: str,
    html: bool,
) -> None:
    from . import _graph

    message: dict = {
        "subject": subject,
        "body": {"contentType": "HTML" if html else "Text", "content": body},
        "toRecipients": _recipients(to_list),
    }
    if cc_list:
        message["ccRecipients"] = _recipients(cc_list)

    _graph.post(
        f"/users/{sender}/sendMail",
        json={"message": message, "saveToSentItems": True},
    )
