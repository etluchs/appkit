"""appkit – the UZH internal-app toolkit.

Four small modules, one job each, all authenticating with the app's **managed
identity** in production and all backed by an in-memory fake for local dev and
tests:

* :mod:`appkit.sharepoint` – read rows from a SharePoint list (Graph)
* :mod:`appkit.mail`        – send mail (Graph ``sendMail``)
* :mod:`appkit.db`          – Postgres with a pool and dict rows (psycopg)
* :mod:`appkit.auth`        – the signed-in :class:`~appkit.auth.User`
                              from Container Apps Easy Auth headers

Application code imports these modules. It must **never** import ``httpx`` or
``psycopg`` directly – appkit owns those integrations.

The active backend is chosen by ``APPKIT_BACKEND`` (``fake`` by default,
``azure`` in production). See :mod:`appkit.config`.
"""

from __future__ import annotations

from . import auth, config, db, mail, sharepoint
from .auth import User, user
from .config import backend, is_fake

__all__ = [
    "auth",
    "config",
    "db",
    "mail",
    "sharepoint",
    "User",
    "user",
    "backend",
    "is_fake",
    "reset_fakes",
]

__version__ = "0.1.0"


def reset_fakes() -> None:
    """Reset all in-memory fake state to its deterministic seed.

    Intended for test fixtures. No-op semantics are safe to call in any backend.
    """
    from . import _fake
    from .db import _reset_fake

    _fake.reset()
    _reset_fake()
