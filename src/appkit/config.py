"""Runtime configuration for appkit.

appkit runs in one of two backends:

* ``fake``  – pure-Python, in-memory. No network, no Azure credentials.
              This is the default for local dev and the test suite.
* ``azure`` – talks to Microsoft Graph and Azure Postgres using the app's
              **managed identity** (``DefaultAzureCredential``).

Select the backend with the ``APPKIT_BACKEND`` environment variable.

The backend is chosen **strictly**. An unset variable means ``fake`` only when
we are clearly *not* running on an Azure app platform; on Container Apps or App
Service an unset or unrecognised value raises :class:`~appkit.errors.ConfigError`
at first use instead of quietly serving in-memory data.

That strictness matters because every fake failure looks like a success:
``send_mail`` would return normally without sending anything, ``db.execute``
would write to an in-memory database that disappears on the next restart,
``list_rows`` would render seed data as if it were real, and ``auth.user``
would hand back an authenticated dev user carrying whatever roles
``APPKIT_DEV_ROLES`` names. A misconfigured production app must fail loudly, not
pretend.
"""

from __future__ import annotations

import os

from .errors import ConfigError

FAKE = "fake"
AZURE = "azure"

#: Environment variables the Azure app platforms inject into every container.
#: Their presence means "this is a real deployment", so guessing is not allowed.
_PLATFORM_MARKERS = ("CONTAINER_APP_NAME", "WEBSITE_SITE_NAME")


def on_azure_platform() -> bool:
    """True when running inside Azure Container Apps / App Service."""
    return any(os.getenv(marker) for marker in _PLATFORM_MARKERS)


def backend() -> str:
    """Return the active backend name (``"fake"`` or ``"azure"``).

    Raises:
        ConfigError: if ``APPKIT_BACKEND`` holds an unrecognised value, or if it
            is unset while running on an Azure app platform.
    """
    raw = os.getenv("APPKIT_BACKEND")
    value = (raw or "").strip().lower()

    if value in (FAKE, AZURE):
        return value

    if value:
        raise ConfigError(
            f"APPKIT_BACKEND={raw!r} is not a valid backend. "
            f"Use {AZURE!r} in production or {FAKE!r} for local development."
        )

    if on_azure_platform():
        raise ConfigError(
            "APPKIT_BACKEND is not set, but this app is running on an Azure app "
            "platform. Set APPKIT_BACKEND=azure to use Microsoft Graph and Azure "
            "Postgres, or APPKIT_BACKEND=fake to deliberately run on in-memory "
            "data. appkit refuses to guess: the fake backend silently discards "
            "mail and database writes and authenticates a dev user."
        )

    return FAKE


def is_fake() -> bool:
    """True when running against the in-memory fake backend."""
    return backend() == FAKE


def env(name: str, default: str | None = None, *, required: bool = False) -> str | None:
    """Read an environment variable, optionally requiring it in ``azure`` mode."""
    value = os.getenv(name, default)
    if required and not value:
        raise ConfigError(
            f"{name} must be set when APPKIT_BACKEND=azure. "
            "See the appkit README for the required configuration."
        )
    return value
