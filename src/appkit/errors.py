"""Exceptions raised by appkit.

Application code that wants to react to a failure (rather than let it become a
500) catches these. Everything appkit raises deliberately derives from
:class:`AppkitError`::

    from appkit import GraphError, sharepoint

    try:
        rows = sharepoint.list_rows("Requests")
    except GraphError as exc:
        if exc.status == 403:
            ...   # the app's identity is missing a Graph permission
"""

from __future__ import annotations


class AppkitError(RuntimeError):
    """Base class for every error appkit raises on purpose."""


class ConfigError(AppkitError):
    """The environment is not configured the way the active backend needs."""


class GraphError(AppkitError):
    """A Microsoft Graph request failed.

    Carries the pieces you actually need to diagnose it: the HTTP status, the
    Graph error ``code`` and ``message`` from the response body, and the
    ``request-id`` header (this is the value Microsoft support asks for).
    """

    def __init__(
        self,
        *,
        status: int,
        method: str,
        url: str,
        code: str = "",
        message: str = "",
        request_id: str = "",
    ) -> None:
        self.status = status
        self.method = method
        self.url = url
        self.code = code
        self.message = message
        self.request_id = request_id
        super().__init__(self._describe())

    def _describe(self) -> str:
        detail = " - ".join(part for part in (self.code, self.message) if part)
        text = f"Graph {self.method} {self.url} failed: {self.status}"
        if detail:
            text += f" ({detail})"
        if self.request_id:
            text += f" [request-id: {self.request_id}]"
        if hint := _hint(self.status):
            text += f"\n{hint}"
        return text


def _hint(status: int) -> str:
    if status in (401, 403):
        return (
            "Hint: the app's managed identity is probably missing a Graph "
            "permission (Sites.Read.All for SharePoint, Mail.Send for mail), or "
            "admin consent for it has not been granted."
        )
    if status == 404:
        return (
            "Hint: check APPKIT_SHAREPOINT_SITE and the list key. Graph resolves "
            "a list by its id or its URL name, not by its display name."
        )
    if status == 429:
        return "Hint: Graph is throttling this app; appkit already retried."
    return ""
