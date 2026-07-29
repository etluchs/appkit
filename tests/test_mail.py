import pytest

from appkit import mail


def test_send_mail_lands_in_outbox():
    mail.send_mail(to="team@uzh.ch", subject="Hello", body="Body text")
    sent = mail.outbox()
    assert len(sent) == 1
    assert sent[0]["to"] == ["team@uzh.ch"]
    assert sent[0]["subject"] == "Hello"
    assert sent[0]["html"] is False


def test_send_mail_accepts_multiple_recipients_and_cc():
    mail.send_mail(
        to=["a@uzh.ch", "b@uzh.ch"],
        cc="boss@uzh.ch",
        subject="Report",
        body="<p>hi</p>",
        html=True,
    )
    msg = mail.outbox()[0]
    assert msg["to"] == ["a@uzh.ch", "b@uzh.ch"]
    assert msg["cc"] == ["boss@uzh.ch"]
    assert msg["html"] is True


def test_send_mail_requires_a_recipient():
    with pytest.raises(ValueError):
        mail.send_mail(to=[], subject="x", body="y")


def test_outbox_is_isolated_between_tests():
    # Thanks to the autouse reset fixture, the outbox starts empty.
    assert mail.outbox() == []
