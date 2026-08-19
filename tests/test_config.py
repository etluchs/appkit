"""Backend selection must never be guessed on a real deployment.

Every fake failure looks like a success — mail vanishes, database writes vanish,
seed data renders as real, and `auth.user` returns an authenticated dev user —
so a misconfigured production app has to fail loudly instead.
"""

import pytest

from appkit import ConfigError, config


def test_defaults_to_fake_off_platform(monkeypatch):
    monkeypatch.delenv("APPKIT_BACKEND", raising=False)
    assert config.backend() == "fake"


def test_explicit_values_win(monkeypatch):
    monkeypatch.setenv("APPKIT_BACKEND", "  AZURE ")
    assert config.backend() == "azure"
    monkeypatch.setenv("APPKIT_BACKEND", "Fake")
    assert config.backend() == "fake"


def test_unrecognised_value_is_an_error(monkeypatch):
    """A typo used to silently select the fake backend."""
    monkeypatch.setenv("APPKIT_BACKEND", "azue")

    with pytest.raises(ConfigError, match="not a valid backend"):
        config.backend()


@pytest.mark.parametrize("marker", ["CONTAINER_APP_NAME", "WEBSITE_SITE_NAME"])
def test_unset_on_an_azure_platform_is_an_error(monkeypatch, marker):
    monkeypatch.delenv("APPKIT_BACKEND", raising=False)
    monkeypatch.setenv(marker, "some-app")

    with pytest.raises(ConfigError, match="APPKIT_BACKEND is not set"):
        config.backend()


def test_fake_can_still_be_chosen_deliberately_on_platform(monkeypatch):
    monkeypatch.setenv("CONTAINER_APP_NAME", "some-app")
    monkeypatch.setenv("APPKIT_BACKEND", "fake")

    assert config.backend() == "fake"


def test_required_env_reports_the_missing_variable(monkeypatch):
    monkeypatch.delenv("APPKIT_DB_DSN", raising=False)

    with pytest.raises(ConfigError, match="APPKIT_DB_DSN"):
        config.env("APPKIT_DB_DSN", required=True)
