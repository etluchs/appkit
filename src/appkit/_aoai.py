"""Thin Azure OpenAI client used by :mod:`appkit.embeddings`.

This is the only place in appkit that speaks HTTP to Azure OpenAI, and it is
built to the same shape as :mod:`appkit._graph`: authenticate with the app's
managed identity, retry the failures that are worth retrying, and turn anything
else into an error that carries what the response body said.

Application code never imports this -- it calls
:func:`appkit.embeddings.embed`.
"""

from __future__ import annotations

import time
from typing import Any

from .errors import AzureOpenAIError

#: The token audience for any Azure AI / Cognitive Services data-plane call.
AOAI_SCOPE = "https://cognitiveservices.azure.com/.default"

#: Default REST API version. Pinned rather than "latest" so an upstream change
#: cannot alter this app's behaviour without a deliberate edit.
DEFAULT_API_VERSION = "2023-05-15"

_TIMEOUT = 60.0
_MAX_ATTEMPTS = 4
_BACKOFF_BASE = 0.5
_BACKOFF_CAP = 60.0

# Embedding requests are pure reads: repeating one produces the same vectors
# and costs a few thousandths of a cent, so every transient status is retried.
_RETRY = frozenset({408, 429, 500, 502, 503, 504})

_sleep = time.sleep  # module attribute so tests can neutralise the backoff


def _headers() -> dict[str, str]:
    from ._credential import token

    return {
        "Authorization": f"Bearer {token(AOAI_SCOPE)}",
        "Accept": "application/json",
        "Content-Type": "application/json",
    }


def _retry_after(response: Any, attempt: int) -> float:
    raw = response.headers.get("retry-after", "")
    try:
        return min(float(raw), _BACKOFF_CAP)
    except (TypeError, ValueError):
        return min(_BACKOFF_BASE * (2**attempt), _BACKOFF_CAP)


def _fail(response: Any, url: str) -> AzureOpenAIError:
    code = message = ""
    try:
        error = response.json().get("error", {})
        if isinstance(error, dict):
            code = str(error.get("code", ""))
            message = str(error.get("message", ""))
    except ValueError:
        message = (response.text or "")[:500]

    return AzureOpenAIError(
        status=response.status_code,
        url=url,
        code=code,
        message=message,
        request_id=response.headers.get("apim-request-id")
        or response.headers.get("x-request-id")
        or "",
    )


def post(url: str, payload: dict[str, Any]) -> dict[str, Any]:
    """POST ``payload`` to ``url`` and return the parsed JSON response."""
    import httpx

    last_connect_error: Exception | None = None

    with httpx.Client(timeout=_TIMEOUT) as client:
        for attempt in range(_MAX_ATTEMPTS):
            try:
                response = client.post(url, json=payload, headers=_headers())
            except httpx.ConnectError as exc:
                last_connect_error = exc
                if attempt + 1 == _MAX_ATTEMPTS:
                    raise
                _sleep(min(_BACKOFF_BASE * (2**attempt), _BACKOFF_CAP))
                continue

            if response.status_code in _RETRY and attempt + 1 < _MAX_ATTEMPTS:
                _sleep(_retry_after(response, attempt))
                continue

            if response.status_code >= 400:
                raise _fail(response, url)
            return response.json()

    raise last_connect_error or RuntimeError("unreachable")
