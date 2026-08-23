"""Contract tests for the directory's Graph code path.

Drives the real filter building, header handling and pagination against a
mocked transport; only the managed-identity token is stubbed.
"""

import httpx
import pytest
import respx

from appkit import GraphError, directory
from appkit._graph import GRAPH_BASE

USERS = f"{GRAPH_BASE}/users"


def _user(shortname, display_name, mail=None, employee_type="hauptamtlich"):
    # A real directory entry has a mailbox; one without is filtered out, so
    # defaulting to blank here would silently empty most of these fixtures.
    mail = f"{shortname}@uzh.ch" if mail is None else mail
    return {
        "id": f"u-{shortname}",
        "displayName": display_name,
        "mail": mail,
        "onPremisesSamAccountName": shortname,
        "employeeType": employee_type,
    }


@respx.mock
def test_filters_by_prefix_across_every_searched_field(azure_backend):
    respx.get(USERS).mock(return_value=httpx.Response(200, json={"value": []}))

    directory.search_people("nicole")

    query = respx.calls.last.request.url.params
    assert query["$filter"] == (
        "(startsWith(displayName,'nicole') or startsWith(mail,'nicole') or "
        "startsWith(onPremisesSamAccountName,'nicole') or startsWith(givenName,'nicole') or "
        "startsWith(surname,'nicole'))"
    )
    assert query["$select"] == "id,displayName,mail,onPremisesSamAccountName,employeeType"


@respx.mock
def test_every_token_becomes_an_and_clause(azure_backend):
    respx.get(USERS).mock(return_value=httpx.Response(200, json={"value": []}))

    directory.search_people("nicole hartmann")

    assert " and " in respx.calls.last.request.url.params["$filter"]


@respx.mock
def test_sends_the_advanced_query_header(azure_backend):
    """Graph refuses `$filter` on onPremisesSamAccountName without it."""
    respx.get(USERS).mock(return_value=httpx.Response(200, json={"value": []}))

    directory.search_people("nicole")

    assert respx.calls.last.request.headers["consistencylevel"] == "eventual"


@respx.mock
def test_the_header_survives_pagination(azure_backend):
    page2 = f"{USERS}?$skiptoken=abc"
    respx.get(USERS).mock(
        side_effect=[
            httpx.Response(
                200,
                json={"value": [_user("a", "Anna Muster")], "@odata.nextLink": page2},
            ),
            httpx.Response(200, json={"value": [_user("b", "Anna Berger")]}),
        ]
    )

    people = directory.search_people("anna")

    assert [p.shortname for p in people] == ["b", "a"]  # Berger sorts first
    assert all(c.request.headers.get("consistencylevel") == "eventual" for c in respx.calls)


@respx.mock
def test_a_quote_in_the_term_cannot_break_out_of_the_filter(azure_backend):
    respx.get(USERS).mock(return_value=httpx.Response(200, json={"value": []}))

    directory.search_people("o'brien")

    assert "startsWith(displayName,'o''brien')" in respx.calls.last.request.url.params["$filter"]


@respx.mock
def test_ranking_is_applied_to_graph_results_too(azure_backend):
    respx.get(USERS).mock(
        return_value=httpx.Response(
            200,
            json={
                "value": [
                    _user("arossi", "Alessandra Rossi"),
                    _user("smeier", "Sandra Meier"),
                ]
            },
        )
    )

    assert [p.display_name for p in directory.search_people("sandra")] == [
        "Sandra Meier",
        "Alessandra Rossi",
    ]


@respx.mock
def test_an_employee_filter_can_be_pushed_to_the_server(azure_backend, monkeypatch):
    monkeypatch.setenv("APPKIT_DIRECTORY_EMPLOYEE_FILTER", "startsWith(employeeType,'h')")
    respx.get(USERS).mock(return_value=httpx.Response(200, json={"value": []}))

    directory.search_people("nicole")

    assert "startsWith(employeeType,'h')" in respx.calls.last.request.url.params["$filter"]


@respx.mock
def test_person_by_shortname_asks_for_an_exact_match(azure_backend):
    respx.get(USERS).mock(
        return_value=httpx.Response(200, json={"value": [_user("nhartma", "Nicole Hartmann")]})
    )

    assert directory.person_by_shortname("nhartma").display_name == "Nicole Hartmann"
    assert (
        respx.calls.last.request.url.params["$filter"]
        == "onPremisesSamAccountName eq 'nhartma'"
    )


@respx.mock
def test_person_by_id_treats_404_as_absent(azure_backend):
    respx.get(f"{USERS}/u-gone").mock(
        return_value=httpx.Response(404, json={"error": {"code": "Request_ResourceNotFound"}})
    )

    assert directory.person_by_id("u-gone") is None


@respx.mock
def test_a_missing_permission_surfaces_as_a_graph_error(azure_backend):
    respx.get(USERS).mock(
        return_value=httpx.Response(403, json={"error": {"code": "Authorization_RequestDenied"}})
    )

    with pytest.raises(GraphError):
        directory.search_people("nicole")
