"""Mail against real Graph.

Sending reaches a real mailbox, so every test here is opt-in via
``APPKIT_LIVE_MAIL_TO``. Subjects are tagged so anything that lands is obviously
a test and traceable to a run.
"""

import os
import uuid

import pytest

from appkit import GraphError, mail

pytestmark = pytest.mark.live

TAG = "[appkit-live]"


def _subject(what: str) -> str:
    return f"{TAG} {what} {uuid.uuid4().hex[:8]}"


def test_a_plain_message_is_accepted(mail_recipient):
    mail.send_mail(
        to=mail_recipient,
        subject=_subject("plain"),
        body="Sent by appkit's live suite. Nothing to do.",
    )


def test_an_html_message_is_accepted(mail_recipient):
    mail.send_mail(
        to=mail_recipient,
        subject=_subject("html"),
        body="<p>Sent by appkit's live suite. <strong>Nothing to do.</strong></p>",
        html=True,
    )


def test_cc_is_accepted(mail_recipient):
    mail.send_mail(
        to=mail_recipient,
        cc=mail_recipient,
        subject=_subject("cc"),
        body="Sent by appkit's live suite.",
    )


def test_sending_from_a_mailbox_the_app_may_not_use_is_refused(mail_recipient):
    """Mail.Send is tenant-wide unless an ApplicationAccessPolicy scopes it.

    If this passes, the identity can send as *any* mailbox in the tenant — which
    is worth knowing, so the test says so rather than failing quietly.
    """
    other = os.getenv("APPKIT_LIVE_FORBIDDEN_SENDER")
    if not other:
        pytest.skip("APPKIT_LIVE_FORBIDDEN_SENDER is not set")

    try:
        mail.send_mail(
            to=mail_recipient,
            subject=_subject("should-not-send"),
            body="If you received this, Mail.Send is not scoped to one mailbox.",
            sender=other,
        )
    except GraphError as exc:
        assert exc.status in (403, 404)
        return

    pytest.fail(
        f"the identity sent mail as {other}. Mail.Send is not restricted by an "
        "Exchange ApplicationAccessPolicy, so this app can send as anyone."
    )
