"""Contract tests for ``sharepoint.list_rows`` on the azure backend.

These pin the exact Graph request appkit makes, and record where the fake and
the real backend disagree — divergence is what breaks an app that was only ever
run locally.
"""

import httpx
import pytest
import respx

from appkit import ConfigError, GraphError, sharepoint
from appkit._graph import GRAPH_BASE

SITE = "contoso.sharepoint.com,site-id,web-id"
ITEMS = f"{GRAPH_BASE}/sites/{SITE}/lists/Requests/items"


def _items(*rows):
    return httpx.Response(200, json={"value": list(rows)})


def _item(item_id, **fields):
    return {"id": item_id, "fields": {"id": item_id, **fields}}


@pytest.fixture
def site_env(monkeypatch):
    monkeypatch.setenv("APPKIT_SHAREPOINT_SITE", SITE)


@respx.mock
def test_requests_the_expected_url_and_params(azure_backend, site_env):
    respx.get(ITEMS).mock(return_value=_items(_item("1", Title="One")))

    rows = sharepoint.list_rows("Requests")

    url = respx.calls.last.request.url
    assert url.path == f"/v1.0/sites/{SITE}/lists/Requests/items"
    assert url.params["expand"] == "fields"
    assert url.params["$top"] == "200"
    assert rows == [{"id": "1", "Title": "One"}]


@respx.mock
def test_select_is_pushed_into_the_expand_clause(azure_backend, site_env):
    respx.get(ITEMS).mock(return_value=_items(_item("1", Title="One")))

    sharepoint.list_rows("Requests", select=["Title", "Status"])

    assert respx.calls.last.request.url.params["expand"] == "fields($select=Title,Status)"


@respx.mock
def test_top_is_forwarded(azure_backend, site_env):
    respx.get(ITEMS).mock(return_value=_items())

    sharepoint.list_rows("Requests", top=50)

    assert respx.calls.last.request.url.params["$top"] == "50"


@respx.mock
def test_site_argument_overrides_the_environment(azure_backend, site_env):
    other = "other.sharepoint.com,a,b"
    respx.get(f"{GRAPH_BASE}/sites/{other}/lists/Requests/items").mock(return_value=_items())

    sharepoint.list_rows("Requests", site=other)

    assert other in str(respx.calls.last.request.url)


def test_missing_site_configuration_fails_loudly(azure_backend, monkeypatch):
    monkeypatch.delenv("APPKIT_SHAREPOINT_SITE", raising=False)

    with pytest.raises(ConfigError, match="APPKIT_SHAREPOINT_SITE"):
        sharepoint.list_rows("Requests")


@respx.mock
def test_pagination_is_followed(azure_backend, site_env):
    respx.get(ITEMS).mock(
        side_effect=[
            httpx.Response(
                200,
                json={
                    "value": [_item("1", Title="One")],
                    "@odata.nextLink": f"{ITEMS}?$skiptoken=x",
                },
            ),
            _items(_item("2", Title="Two")),
        ]
    )

    rows = sharepoint.list_rows("Requests")

    assert [r["Title"] for r in rows] == ["One", "Two"]


@respx.mock
def test_id_falls_back_to_the_item_id_when_fields_omit_it(azure_backend, site_env):
    respx.get(ITEMS).mock(return_value=_items({"id": "7", "fields": {"Title": "Seven"}}))

    assert sharepoint.list_rows("Requests") == [{"Title": "Seven", "id": "7"}]


@respx.mock
def test_graph_failure_surfaces_as_graph_error(azure_backend, site_env):
    respx.get(ITEMS).mock(
        return_value=httpx.Response(
            403, json={"error": {"code": "accessDenied", "message": "Access denied."}}
        )
    )

    with pytest.raises(GraphError) as exc:
        sharepoint.list_rows("Requests")

    assert exc.value.status == 403


# --------------------------------------------------------------------------
# Known fake-vs-azure divergences. These are pinned deliberately: an app built
# against the fake can hit any of them the first time it runs in Azure.
# --------------------------------------------------------------------------

@respx.mock
def test_divergence_select_of_an_unknown_field(azure_backend, site_env):
    """The fake invents ``None``; Graph simply omits the field."""
    respx.get(ITEMS).mock(return_value=_items(_item("1", Title="One")))

    azure_rows = sharepoint.list_rows("Requests", select=["Title", "Nope"])
    assert azure_rows == [{"id": "1", "Title": "One"}]  # no "Nope" key at all


def test_divergence_select_of_an_unknown_field_on_the_fake():
    rows = sharepoint.list_rows("Requests", select=["Title", "Nope"])
    assert rows[0]["Nope"] is None  # the fake fabricates the key


def test_divergence_list_key_is_a_display_name_on_the_fake():
    """The fake keys lists by display name.

    Graph resolves ``/lists/{key}`` by list **id** or **URL name**, so a display
    name containing a space works locally and 404s in Azure. Pinned so the
    difference is visible; see the README note on naming SharePoint lists.
    """
    from appkit import _fake

    _fake.set_sharepoint_rows("My Requests", [{"id": "1"}])
    assert sharepoint.list_rows("My Requests") == [{"id": "1"}]


def test_divergence_site_and_top_are_ignored_by_the_fake():
    rows = sharepoint.list_rows("Requests", site="ignored", top=1)
    assert len(rows) == 4  # `top` had no effect; `site` was never consulted
