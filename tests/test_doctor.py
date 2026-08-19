"""The doctor is what you run when something is already wrong.

So the bar is: it never crashes, one broken check never hides the others, and it
never prints a token, password or connection string.
"""

import base64
import json

import httpx
import pytest
import respx

from appkit import doctor
from appkit._graph import GRAPH_BASE

SITE = "contoso.sharepoint.com,site-id,web-id"


def _token(**claims) -> str:
    """An unsigned JWT-shaped token. The doctor only reads it, never trusts it."""
    payload = base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip("=")
    return f"header.{payload}.signature"


APP_TOKEN = _token(
    appid="99999999-8888-7777-6666-555555555555",
    oid="11111111-2222-3333-4444-555555555555",
    tid="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
    roles=["Sites.Selected", "Mail.Send"],
)
USER_TOKEN = _token(oid="user-oid", tid="tenant", scp="Sites.Read.All User.Read")


@pytest.fixture
def azure(monkeypatch):
    monkeypatch.setenv("APPKIT_BACKEND", "azure")
    monkeypatch.setenv("APPKIT_AUTH", "easyauth")
    monkeypatch.setenv("APPKIT_SHAREPOINT_SITE", SITE)
    monkeypatch.delenv("APPKIT_MAIL_SENDER", raising=False)
    monkeypatch.delenv("APPKIT_DB_DSN", raising=False)
    monkeypatch.setattr("appkit._credential.token", lambda scope: APP_TOKEN)
    monkeypatch.setattr("appkit._graph._sleep", lambda seconds: None)


def _mock_site(lists=()):
    respx.get(f"{GRAPH_BASE}/sites/{SITE}").mock(
        return_value=httpx.Response(200, json={"displayName": "Requests Team"})
    )
    respx.get(f"{GRAPH_BASE}/sites/{SITE}/lists").mock(
        return_value=httpx.Response(200, json={"value": list(lists)})
    )


def _by_name(checks):
    return {check.name: check for check in checks}


# --- the fake backend -----------------------------------------------------

def test_on_the_fake_backend_nothing_is_contacted():
    checks = _by_name(doctor.run())

    assert checks["backend"].status == doctor.PASS
    assert "fake" in checks["backend"].detail
    assert checks["integrations"].status == doctor.SKIP
    assert "credential" not in checks


def test_fake_backend_on_a_platform_is_flagged(monkeypatch):
    """The quiet catastrophe: a real deployment serving in-memory data."""
    monkeypatch.setenv("CONTAINER_APP_NAME", "adate")
    monkeypatch.setenv("APPKIT_BACKEND", "fake")

    backend = _by_name(doctor.run())["backend"]

    assert backend.status == doctor.WARN
    assert "discarded" in backend.hint


def test_a_broken_backend_setting_stops_early(monkeypatch):
    monkeypatch.setenv("APPKIT_BACKEND", "azue")

    checks = doctor.run()

    assert _by_name(checks)["backend"].status == doctor.FAIL
    assert "credential" not in _by_name(checks)


# --- identity -------------------------------------------------------------

@respx.mock
def test_reports_an_application_token_and_its_roles(azure):
    _mock_site()

    credential = _by_name(doctor.run())["credential"]

    assert credential.status == doctor.PASS
    assert "application (app-only)" in credential.detail
    assert any("Mail.Send, Sites.Selected" in note for note in credential.notes)
    # The two ids you need for the app-role assignment and the site grant.
    body = "\n".join(credential.notes)
    assert "client id (appid) = 99999999" in body
    assert "object id (oid)   = 11111111" in body


@respx.mock
def test_a_delegated_token_is_called_out(azure, monkeypatch):
    monkeypatch.setattr("appkit._credential.token", lambda scope: USER_TOKEN)
    _mock_site()

    credential = _by_name(doctor.run())["credential"]

    assert "delegated" in credential.detail
    assert "app-only permission model" in credential.hint


@respx.mock
def test_no_credential_available_is_a_failure_with_a_hint(azure, monkeypatch):
    def explode(scope):
        raise RuntimeError("ManagedIdentityCredential authentication unavailable")

    monkeypatch.setattr("appkit._credential.token", explode)
    _mock_site()

    credential = _by_name(doctor.run())["credential"]

    assert credential.status == doctor.FAIL
    assert "az login" in credential.hint


# --- sharepoint -----------------------------------------------------------

@respx.mock
def test_reports_the_list_keys_graph_will_actually_accept(azure):
    """The display-name trap: appkit's fake accepts one, Graph accepts the other."""
    _mock_site([{"name": "requests", "displayName": "My Requests", "id": "abc-123"}])

    sharepoint = _by_name(doctor.run())["sharepoint"]

    assert sharepoint.status == doctor.PASS
    body = "\n".join(sharepoint.notes)
    assert "not the display name" in body
    assert "'requests'" in body and "'My Requests'" in body and "abc-123" in body


@respx.mock
def test_a_site_that_cannot_be_read_fails_without_hiding_later_checks(azure):
    respx.get(f"{GRAPH_BASE}/sites/{SITE}").mock(
        return_value=httpx.Response(403, json={"error": {"code": "accessDenied"}})
    )

    checks = _by_name(doctor.run())

    assert checks["sharepoint"].status == doctor.FAIL
    assert checks["mail"].status == doctor.SKIP     # still reported
    assert checks["database"].status == doctor.SKIP


@respx.mock
def test_reads_a_named_list_when_asked(azure):
    _mock_site([{"name": "requests", "displayName": "Requests", "id": "1"}])
    respx.get(f"{GRAPH_BASE}/sites/{SITE}/lists/requests/items").mock(
        return_value=httpx.Response(200, json={"value": [{"id": "1", "fields": {}}]})
    )

    sharepoint = _by_name(doctor.run(list_name="requests"))["sharepoint"]

    assert sharepoint.status == doctor.PASS
    assert "returned 1 rows" in sharepoint.detail


# --- mail -----------------------------------------------------------------

@respx.mock
def test_mail_is_not_sent_unless_asked(azure, monkeypatch):
    monkeypatch.setenv("APPKIT_MAIL_SENDER", "app@uzh.ch")
    _mock_site()
    route = respx.post(f"{GRAPH_BASE}/users/app@uzh.ch/sendMail")

    mail = _by_name(doctor.run())["mail"]

    assert mail.status == doctor.SKIP
    assert not route.called


@respx.mock
def test_sending_proves_the_mailbox(azure, monkeypatch):
    monkeypatch.setenv("APPKIT_MAIL_SENDER", "app@uzh.ch")
    _mock_site()
    respx.post(f"{GRAPH_BASE}/users/app@uzh.ch/sendMail").mock(
        return_value=httpx.Response(202)
    )

    mail = _by_name(doctor.run(send_to="you@uzh.ch"))["mail"]

    assert mail.status == doctor.PASS


@respx.mock
def test_a_blocked_mailbox_points_at_the_access_policy(azure, monkeypatch):
    monkeypatch.setenv("APPKIT_MAIL_SENDER", "app@uzh.ch")
    _mock_site()
    respx.post(f"{GRAPH_BASE}/users/app@uzh.ch/sendMail").mock(
        return_value=httpx.Response(403, json={"error": {"code": "ErrorAccessDenied"}})
    )

    mail = _by_name(doctor.run(send_to="you@uzh.ch"))["mail"]

    assert mail.status == doctor.FAIL
    assert "ApplicationAccessPolicy" in mail.hint


# --- output ---------------------------------------------------------------

@respx.mock
def test_never_prints_the_token_or_the_dsn(azure, monkeypatch, capsys):
    secret_dsn = "postgresql://appkit@db.postgres.database.azure.com/appkit?sslmode=require"
    monkeypatch.setenv("APPKIT_DB_DSN", secret_dsn)
    monkeypatch.setattr("appkit.db.query", lambda *a, **k: (_ for _ in ()).throw(
        RuntimeError("connection refused")
    ))
    _mock_site()

    doctor.main([])
    out = capsys.readouterr().out

    assert APP_TOKEN not in out
    assert "db.postgres.database.azure.com" not in out


@respx.mock
def test_json_output_is_machine_readable(azure, capsys):
    _mock_site()

    doctor.main(["--json"])
    payload = json.loads(capsys.readouterr().out)

    assert {c["name"] for c in payload["checks"]} >= {"backend", "auth", "credential"}
    assert all({"name", "status", "detail"} <= set(c) for c in payload["checks"])


@respx.mock
def test_exit_code_reports_failure(azure, capsys):
    respx.get(f"{GRAPH_BASE}/sites/{SITE}").mock(return_value=httpx.Response(403))

    assert doctor.main([]) == 1
    assert "1 failed" in capsys.readouterr().out


@respx.mock
def test_exit_code_is_zero_when_only_skips_and_warnings(azure, capsys):
    _mock_site()

    assert doctor.main([]) == 0
    assert "all checks passed" in capsys.readouterr().out
