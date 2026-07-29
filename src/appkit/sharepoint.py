"""Read rows from a SharePoint list via Microsoft Graph.

Public surface::

    from appkit import sharepoint
    rows = sharepoint.list_rows("Requests")

Each row is a plain ``dict`` of the list item's fields (the Graph
``fields`` facet), plus an ``id`` key. In ``fake`` mode the rows come from
:mod:`appkit._fake`; in ``azure`` mode they come from Graph, authenticated
with the app's managed identity.
"""

from __future__ import annotations

from typing import Any

from .config import env, is_fake


def list_rows(
    list_name: str,
    *,
    site: str | None = None,
    select: list[str] | None = None,
    top: int = 200,
) -> list[dict[str, Any]]:
    """Return the items of a SharePoint list as a list of field dicts.

    Args:
        list_name: Display name or ID of the SharePoint list.
        site: Graph site id (``host,siteId,webId`` or ``host:/sites/x``).
            Defaults to ``APPKIT_SHAREPOINT_SITE``.
        select: Optional list of field names to project.
        top: Page size hint for Graph (pagination is followed automatically).
    """
    if is_fake():
        rows = _fake_rows(list_name)
        if select:
            rows = [{k: r.get(k) for k in ("id", *select)} for r in rows]
        return rows

    site = site or env("APPKIT_SHAREPOINT_SITE", required=True)
    return _graph_rows(list_name, site=site, select=select, top=top)


def _fake_rows(list_name: str) -> list[dict[str, Any]]:
    from . import _fake

    return _fake.sharepoint_rows(list_name)


def _graph_rows(
    list_name: str,
    *,
    site: str,
    select: list[str] | None,
    top: int,
) -> list[dict[str, Any]]:
    from . import _graph

    params: dict[str, Any] = {"expand": "fields", "$top": top}
    if select:
        params["expand"] = f"fields($select={','.join(select)})"

    items = _graph.get_all(f"/sites/{site}/lists/{list_name}/items", params=params)
    rows: list[dict[str, Any]] = []
    for item in items:
        fields = dict(item.get("fields", {}))
        fields.setdefault("id", item.get("id"))
        rows.append(fields)
    return rows
