"""Managed-identity credential, shared across the Azure-backed modules.

Everything that talks to Azure (Graph, Postgres) authenticates as the app's
**managed identity**. In production that is the user-assigned or system-assigned
identity bound to the Azure Container App; locally (when you deliberately run
with ``APPKIT_BACKEND=azure``) ``DefaultAzureCredential`` also picks up the
Azure CLI / VS Code / environment credentials, which is handy for debugging.

The import of :mod:`azure.identity` is deferred so that the default ``fake``
backend never needs the Azure SDK installed at runtime.
"""

from __future__ import annotations

from functools import lru_cache
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - typing only
    from azure.core.credentials import TokenCredential


@lru_cache(maxsize=1)
def credential() -> TokenCredential:
    """Return a process-wide :class:`DefaultAzureCredential`."""
    from azure.identity import DefaultAzureCredential

    return DefaultAzureCredential()


def token(scope: str) -> str:
    """Fetch a bearer token for ``scope`` using the managed identity."""
    return credential().get_token(scope).token
