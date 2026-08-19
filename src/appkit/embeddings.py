"""Turn text into vectors with Azure OpenAI.

Public surface::

    from appkit import embeddings

    if embeddings.available():
        vectors = embeddings.embed(["a service description", "another one"])

Embeddings let an app compare text by *meaning* rather than by the words it
happens to contain -- a search for "survey" finding a service that only ever
says "Umfrage". The usual pattern for an internal app is small enough to keep
entirely in memory: embed a few hundred records once, embed the query, and take
the highest cosine similarity. No vector database is involved, and appkit
deliberately does not provide one.

In ``azure`` mode this calls the deployment named by
``APPKIT_EMBEDDINGS_DEPLOYMENT`` on ``APPKIT_EMBEDDINGS_ENDPOINT``, using the
app's managed identity. The identity needs the **Cognitive Services OpenAI
User** role on that resource.

Which backend embeddings use normally follows ``APPKIT_BACKEND``, but it can be
pointed the other way on its own with ``APPKIT_EMBEDDINGS_BACKEND`` — see
:func:`backend`. That exists for one real case: developing a search feature
against local fixture data while still getting vectors that mean something.

In ``fake`` mode :func:`available` returns ``False``, and :func:`embed` returns
deterministic pseudo-vectors. Those vectors are stable but **meaningless** --
they exist so the surrounding code path can be exercised offline, never so that
relevance can be tested. An app should treat semantic matching as an
enhancement over a lexical search that works without it, and
:func:`available` is how it asks.
"""

from __future__ import annotations

import hashlib
import math
import os
from typing import Any

from .config import AZURE, FAKE, env
from .config import backend as _app_backend

#: Dimensionality of ``text-embedding-3-small``, and of the fake vectors.
DIMENSIONS = 1536

#: Default deployment name. Azure OpenAI deployments are usually named after
#: the model they serve.
DEFAULT_DEPLOYMENT = "text-embedding-3-small"

#: Inputs per request. The service accepts more, but a smaller batch keeps a
#: single failure from costing the whole refresh and stays clear of the
#: per-request token ceiling.
BATCH_SIZE = 128

#: Points embeddings at a backend of their own, independently of
#: ``APPKIT_BACKEND``. See :func:`backend`.
BACKEND_ENV = "APPKIT_EMBEDDINGS_BACKEND"


def backend() -> str:
    """Which backend embeddings use: ``APPKIT_BACKEND``, unless overridden.

    ``APPKIT_EMBEDDINGS_BACKEND`` exists for one situation that configuration
    could not otherwise express: running an app on the ``fake`` backend — local
    fixture data, no SharePoint, no credentials — while still embedding for
    real, because pseudo-vectors cannot tell you whether a search *ranks* well.
    Without it, tuning a semantic feature means standing up every other
    integration first.

    It is opt-in and **defaults to following APPKIT_BACKEND**, so a test suite
    stays offline unless someone deliberately says otherwise. An unrecognised
    value raises rather than being ignored: silently falling back to
    meaningless vectors is exactly the failure this variable exists to avoid.

    Raises:
        ConfigError: if the variable holds something other than ``fake`` or
            ``azure``.
    """
    raw = os.getenv(BACKEND_ENV)
    value = (raw or "").strip().lower()
    if value in (FAKE, AZURE):
        return value
    if value:
        from .errors import ConfigError

        raise ConfigError(
            f"{BACKEND_ENV}={raw!r} is not a valid backend. Use {AZURE!r} to embed "
            f"for real, {FAKE!r} for deterministic pseudo-vectors, or leave it "
            f"unset to follow APPKIT_BACKEND."
        )
    return _app_backend()


def available() -> bool:
    """True when :func:`embed` returns vectors that carry meaning.

    ``False`` on the fake backend, and ``False`` in ``azure`` mode when no
    endpoint is configured. Call this before offering a semantic feature, and
    fall back to something lexical when it says no -- an app that requires
    embeddings cannot run locally.
    """
    if backend() != AZURE:
        return False
    return bool(os.getenv("APPKIT_EMBEDDINGS_ENDPOINT", "").strip())


def embed(texts: list[str], *, deployment: str | None = None) -> list[list[float]]:
    """Return one vector per input string, in the order given.

    Args:
        texts: The strings to embed. An empty list returns an empty list.
        deployment: Azure OpenAI deployment name. Defaults to
            ``APPKIT_EMBEDDINGS_DEPLOYMENT``, then to
            :data:`DEFAULT_DEPLOYMENT`.

    Raises:
        ConfigError: if ``APPKIT_EMBEDDINGS_ENDPOINT`` is unset in azure mode,
            or ``APPKIT_EMBEDDINGS_BACKEND`` holds an unrecognised value.
        AzureOpenAIError: if the service rejects the request.
    """
    if not texts:
        return []
    if any(not isinstance(text, str) for text in texts):
        raise TypeError("embed() takes a list of strings")

    if backend() != AZURE:
        return [_fake_vector(text) for text in texts]

    endpoint = str(env("APPKIT_EMBEDDINGS_ENDPOINT", required=True)).rstrip("/")
    deployment = (
        deployment
        or os.getenv("APPKIT_EMBEDDINGS_DEPLOYMENT", "").strip()
        or DEFAULT_DEPLOYMENT
    )
    return _embed_via_azure(texts, endpoint=endpoint, deployment=deployment)


def _embed_via_azure(texts: list[str], *, endpoint: str, deployment: str) -> list[list[float]]:
    from . import _aoai

    api_version = (
        os.getenv("APPKIT_EMBEDDINGS_API_VERSION", "").strip() or _aoai.DEFAULT_API_VERSION
    )
    url = f"{endpoint}/openai/deployments/{deployment}/embeddings?api-version={api_version}"

    vectors: list[list[float]] = []
    for start in range(0, len(texts), BATCH_SIZE):
        batch = texts[start : start + BATCH_SIZE]
        payload = _aoai.post(url, {"input": batch})
        vectors.extend(_ordered(payload, expected=len(batch), url=url))
    return vectors


def _ordered(payload: dict[str, Any], *, expected: int, url: str) -> list[list[float]]:
    """Pull the vectors out of a response, restoring the input order.

    The API returns an ``index`` per item and is documented to preserve order,
    but sorting on it explicitly means a future change cannot silently pair the
    wrong vector with the wrong record -- a bug that would look like poor
    relevance rather than like a failure.
    """
    from .errors import AzureOpenAIError

    data = payload.get("data")
    if not isinstance(data, list) or len(data) != expected:
        got = len(data) if isinstance(data, list) else "none"
        raise AzureOpenAIError(
            status=200,
            url=url,
            code="MalformedResponse",
            message=f"expected {expected} embeddings, got {got}",
        )
    ordered = sorted(data, key=lambda item: item.get("index", 0))
    return [list(item["embedding"]) for item in ordered]


def _fake_vector(text: str) -> list[float]:
    """A deterministic unit vector derived from ``text``.

    Derived from a hash, so it is stable across runs and processes -- which is
    what makes tests repeatable -- and carries no semantic information
    whatsoever. Two similar sentences get two unrelated vectors.
    """
    digest = hashlib.sha256(text.encode("utf-8")).digest()
    values: list[float] = []
    counter = 0
    while len(values) < DIMENSIONS:
        block = hashlib.sha256(digest + counter.to_bytes(4, "big")).digest()
        values.extend((byte - 127.5) / 127.5 for byte in block)
        counter += 1
    values = values[:DIMENSIONS]
    norm = math.sqrt(sum(value * value for value in values)) or 1.0
    return [value / norm for value in values]
