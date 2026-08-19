"""SharePoint against real Graph.

The mocked contract tests prove appkit builds the request it intends to. These
prove Graph agrees — that the site resolves, the identity may read it, the
response really has the shape appkit unpacks, and pagination actually triggers.

Assertions are about shapes and invariants, never about tenant data: a test that
asserts "4 rows" breaks the first time somebody files a request.
"""

import pytest

from appkit import GraphError, sharepoint

pytestmark = pytest.mark.live


def test_the_site_resolves_and_is_readable(site):
    from appkit import _graph

    info = _graph.get(f"/sites/{site}")

    assert info.get("id"), "Graph returned a site with no id"
    assert info.get("webUrl", "").startswith("http")


def test_the_identity_can_see_at_least_one_list(site):
    from appkit import _graph

    lists = _graph.get_all(f"/sites/{site}/lists", params={"$top": 50})

    assert lists, "no lists visible — check the Sites.Selected grant for this site"
    assert all("name" in item for item in lists)


def test_rows_come_back_as_field_dicts_with_an_id(list_key):
    rows = sharepoint.list_rows(list_key)

    assert isinstance(rows, list)
    for row in rows:
        assert isinstance(row, dict)
        assert row.get("id"), "every row must carry an id appkit can address it by"


def test_row_values_are_plain_json_types(list_key):
    """Whatever a caller renders in a template has to survive Jinja unchanged."""
    rows = sharepoint.list_rows(list_key)
    if not rows:
        pytest.skip("the list is empty")

    for value in rows[0].values():
        assert isinstance(value, str | int | float | bool | list | dict | type(None))


def test_select_projects_to_the_requested_fields(list_key):
    rows = sharepoint.list_rows(list_key)
    if not rows:
        pytest.skip("the list is empty")

    field = next((key for key in rows[0] if key != "id"), None)
    if field is None:
        pytest.skip("the list has no fields beyond id")

    projected = sharepoint.list_rows(list_key, select=[field])

    assert projected
    for row in projected:
        assert set(row) <= {"id", field}, f"Graph returned more than the selected {field!r}"


def test_pagination_is_followed(list_key):
    """A page size of 1 must still yield every row, via @odata.nextLink."""
    everything = sharepoint.list_rows(list_key)
    if len(everything) < 2:
        pytest.skip("needs at least two rows to force a second page")

    paged = sharepoint.list_rows(list_key, top=1)

    assert len(paged) == len(everything)
    assert {row["id"] for row in paged} == {row["id"] for row in everything}


def test_an_unknown_list_fails_with_a_usable_reason(site):
    """The error path apps actually hit: a display name where a key belongs."""
    with pytest.raises(GraphError) as exc:
        sharepoint.list_rows("no-such-list-cf19a4", site=site)

    error = exc.value
    assert error.status in (400, 404)
    assert error.code, "Graph's error code was lost"
    assert error.request_id, "the request-id Microsoft support asks for was lost"


def test_a_site_the_identity_cannot_read_is_refused():
    """Sites.Selected means access is per site, not tenant-wide."""
    import os

    other = os.getenv("APPKIT_LIVE_FORBIDDEN_SITE")
    if not other:
        pytest.skip("APPKIT_LIVE_FORBIDDEN_SITE is not set")

    with pytest.raises(GraphError) as exc:
        sharepoint.list_rows("anything", site=other)

    assert exc.value.status in (401, 403, 404)
