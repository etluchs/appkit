"""Contract tests for ``appkit.embeddings`` on both backends."""

import json

import httpx
import pytest
import respx

from appkit import AzureOpenAIError, ConfigError, embeddings
from appkit._aoai import DEFAULT_API_VERSION

ENDPOINT = "https://example-aoai.openai.azure.com"
DEPLOYMENT = "text-embedding-3-small"
URL = f"{ENDPOINT}/openai/deployments/{DEPLOYMENT}/embeddings?api-version={DEFAULT_API_VERSION}"


@pytest.fixture
def aoai_env(monkeypatch):
    monkeypatch.setenv("APPKIT_EMBEDDINGS_ENDPOINT", ENDPOINT)
    monkeypatch.delenv("APPKIT_EMBEDDINGS_DEPLOYMENT", raising=False)
    monkeypatch.delenv("APPKIT_EMBEDDINGS_API_VERSION", raising=False)


def _payload(count, *, dims=3, shuffled=False):
    data = [
        {"index": i, "embedding": [float(i)] * dims, "object": "embedding"} for i in range(count)
    ]
    if shuffled:
        data.reverse()
    return {"object": "list", "data": data, "model": DEPLOYMENT}


# --- fake backend ----------------------------------------------------------


def test_fake_is_not_available(fake_backend):
    # An app must be able to tell that these vectors carry no meaning, so it
    # can fall back to lexical search rather than ranking on noise.
    assert embeddings.available() is False


def test_fake_vectors_are_deterministic_and_normalised(fake_backend):
    first = embeddings.embed(["Umfragetool", "Datenbankservice"])
    second = embeddings.embed(["Umfragetool", "Datenbankservice"])

    assert first == second
    assert first[0] != first[1]
    assert len(first[0]) == embeddings.DIMENSIONS
    assert sum(value * value for value in first[0]) == pytest.approx(1.0, rel=1e-6)


def test_empty_input_never_calls_out(fake_backend):
    assert embeddings.embed([]) == []


def test_non_string_input_is_rejected(fake_backend):
    with pytest.raises(TypeError):
        embeddings.embed(["fine", 7])


# --- azure backend ---------------------------------------------------------


def test_available_needs_an_endpoint(azure_backend, monkeypatch):
    monkeypatch.delenv("APPKIT_EMBEDDINGS_ENDPOINT", raising=False)
    assert embeddings.available() is False
    monkeypatch.setenv("APPKIT_EMBEDDINGS_ENDPOINT", ENDPOINT)
    assert embeddings.available() is True


def test_missing_endpoint_raises(azure_backend, monkeypatch):
    monkeypatch.delenv("APPKIT_EMBEDDINGS_ENDPOINT", raising=False)
    with pytest.raises(ConfigError):
        embeddings.embed(["anything"])


@respx.mock
def test_posts_the_inputs_and_returns_vectors(azure_backend, aoai_env):
    route = respx.post(URL).mock(return_value=httpx.Response(200, json=_payload(2)))

    vectors = embeddings.embed(["one", "two"])

    assert route.called
    assert json.loads(respx.calls.last.request.content) == {"input": ["one", "two"]}
    assert vectors == [[0.0, 0.0, 0.0], [1.0, 1.0, 1.0]]


@respx.mock
def test_response_order_follows_the_input(azure_backend, aoai_env):
    # Pairing the wrong vector with the wrong record would show up as bad
    # relevance rather than as an error, so order is restored explicitly.
    respx.post(URL).mock(return_value=httpx.Response(200, json=_payload(3, shuffled=True)))

    vectors = embeddings.embed(["a", "b", "c"])

    assert [vector[0] for vector in vectors] == [0.0, 1.0, 2.0]


@respx.mock
def test_long_input_is_batched(azure_backend, aoai_env, monkeypatch):
    monkeypatch.setattr(embeddings, "BATCH_SIZE", 2)
    sizes = []

    def handler(request):
        batch = json.loads(request.content)["input"]
        sizes.append(len(batch))
        return httpx.Response(200, json=_payload(len(batch)))

    respx.post(URL).mock(side_effect=handler)

    vectors = embeddings.embed(["a", "b", "c", "d", "e"])

    assert sizes == [2, 2, 1]
    assert len(vectors) == 5


@respx.mock
def test_deployment_override(azure_backend, aoai_env):
    url = f"{ENDPOINT}/openai/deployments/other/embeddings?api-version={DEFAULT_API_VERSION}"
    respx.post(url).mock(return_value=httpx.Response(200, json=_payload(1)))

    assert embeddings.embed(["x"], deployment="other")


@respx.mock
def test_throttling_is_retried(azure_backend, aoai_env, monkeypatch):
    monkeypatch.setattr("appkit._aoai._sleep", lambda seconds: None)
    respx.post(URL).mock(
        side_effect=[
            httpx.Response(429, headers={"retry-after": "1"}),
            httpx.Response(200, json=_payload(1)),
        ]
    )

    assert embeddings.embed(["x"]) == [[0.0, 0.0, 0.0]]


@respx.mock
def test_forbidden_explains_the_missing_role(azure_backend, aoai_env):
    respx.post(URL).mock(
        return_value=httpx.Response(
            403, json={"error": {"code": "AuthorizationFailed", "message": "denied"}}
        )
    )

    with pytest.raises(AzureOpenAIError) as caught:
        embeddings.embed(["x"])

    assert caught.value.status == 403
    assert caught.value.code == "AuthorizationFailed"
    assert "Cognitive Services OpenAI User" in str(caught.value)


@respx.mock
def test_unregistered_subscription_is_explained(azure_backend, aoai_env):
    # This is what a caller scoped to the wrong subscription actually gets
    # back, and the message alone does not say what to do about it.
    respx.post(URL).mock(
        return_value=httpx.Response(
            400,
            json={
                "error": {
                    "code": "SubscriptionNotRegistered",
                    "message": "CheckAccess request is invalid",
                }
            },
        )
    )

    with pytest.raises(AzureOpenAIError) as caught:
        embeddings.embed(["x"])

    assert "az provider register" in str(caught.value)


@respx.mock
def test_unknown_deployment_points_at_the_deployment_name(azure_backend, aoai_env):
    respx.post(URL).mock(
        return_value=httpx.Response(
            404, json={"error": {"code": "DeploymentNotFound", "message": "no such deployment"}}
        )
    )

    with pytest.raises(AzureOpenAIError) as caught:
        embeddings.embed(["x"])

    assert "APPKIT_EMBEDDINGS_DEPLOYMENT" in str(caught.value)


@respx.mock
def test_short_response_is_an_error_not_a_misalignment(azure_backend, aoai_env):
    respx.post(URL).mock(return_value=httpx.Response(200, json=_payload(1)))

    with pytest.raises(AzureOpenAIError) as caught:
        embeddings.embed(["a", "b"])

    assert "expected 2 embeddings" in str(caught.value)
