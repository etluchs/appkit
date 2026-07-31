"""Runtime configuration for appkit.

appkit runs in one of two backends:

* ``fake``  – pure-Python, in-memory. No network, no Azure credentials.
              This is the default and is what local dev and the test suite use.
* ``azure`` – talks to Microsoft Graph and Azure Postgres using the app's
              **managed identity** (``DefaultAzureCredential``).

Select the backend with the ``APPKIT_BACKEND`` environment variable. Anything
other than ``azure`` (case-insensitive) is treated as ``fake`` so that a
misconfigured environment fails *safe* (offline) rather than trying to reach
Azure with no credentials.
"""

from __future__ import annotations

import os

FAKE = "fake"
AZURE = "azure"


def backend() -> str:
    """Return the active backend name (``"fake"`` or ``"azure"``)."""
    return AZURE if os.getenv("APPKIT_BACKEND", FAKE).strip().lower() == AZURE else FAKE


def is_fake() -> bool:
    """True when running against the in-memory fake backend."""
    return backend() == FAKE


def env(name: str, default: str | None = None, *, required: bool = False) -> str | None:
    """Read an environment variable, optionally requiring it in ``azure`` mode."""
    value = os.getenv(name, default)
    if required and not value:
        raise RuntimeError(
            f"{name} must be set when APPKIT_BACKEND=azure. "
            "See the appkit README for the required configuration."
        )
    return value
