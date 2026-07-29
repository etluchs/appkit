"""Thin Microsoft Graph client used by the Azure-backed modules.

This is the *only* place in appkit that imports ``httpx`` and speaks HTTP to
Graph. Application code must never do this directly – it calls
:func:`appkit.sharepoint.list_rows` or :func:`appkit.mail.send_mail` instead.

All requests authenticate with the app's managed identity (see
:mod:`appkit._credential`).
"""

from __future__ import annotations

from typing import Any

GRAPH_BASE = "https://graph.microsoft.com/v1.0"
GRAPH_SCOPE = "https://graph.microsoft.com/.default"
_TIMEOUT = 30.0


def _headers() -> dict[str, str]:
    from ._credential import token

    return {
        "Authorization": f"Bearer {token(GRAPH_SCOPE)}",
        "Accept": "application/json",
    }


def get(path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    """GET ``path`` (relative to the Graph base) and return parsed JSON."""
    import httpx

    with httpx.Client(base_url=GRAPH_BASE, timeout=_TIMEOUT) as client:
        response = client.get(path, params=params, headers=_headers())
        response.raise_for_status()
        return response.json()


def get_all(path: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """GET a collection, following ``@odata.nextLink`` pagination."""
    import httpx

    items: list[dict[str, Any]] = []
    with httpx.Client(timeout=_TIMEOUT) as client:
        url: str | None = f"{GRAPH_BASE}{path}"
        query = params
        while url:
            response = client.get(url, params=query, headers=_headers())
            response.raise_for_status()
            payload = response.json()
            items.extend(payload.get("value", []))
            url = payload.get("@odata.nextLink")
            query = None  # nextLink already carries the query
    return items


def post(path: str, json: dict[str, Any]) -> None:
    """POST ``json`` to ``path``; raise for non-2xx. Returns no body."""
    import httpx

    with httpx.Client(base_url=GRAPH_BASE, timeout=_TIMEOUT) as client:
        response = client.post(path, json=json, headers=_headers())
        response.raise_for_status()
