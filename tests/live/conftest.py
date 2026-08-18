"""Fixtures for the live suite.

These tests talk to real Microsoft Graph and real Azure Postgres. They are
deselected by default (see ``addopts`` in pyproject.toml) and run explicitly::

    pytest -m live

The guiding rule is that a live test must never be able to pass against the
fakes — a green run that quietly exercised in-memory data is worse than no run
at all. So the session refuses to start unless the azure backend is active, and
each area skips loudly when its configuration is absent rather than inventing a
substitute.
"""

import os
import uuid

import pytest

from appkit import config


@pytest.fixture(scope="session", autouse=True)
def require_azure_backend():
    """Refuse to run the live suite against the fake backend."""
    if config.backend() != config.AZURE:
        pytest.exit(
            "The live suite needs APPKIT_BACKEND=azure. Running it on the fake "
            "backend would report success without contacting anything.",
            returncode=2,
        )


@pytest.fixture(scope="session")
def site():
    value = os.getenv("APPKIT_SHAREPOINT_SITE")
    if not value:
        pytest.skip("APPKIT_SHAREPOINT_SITE is not set")
    return value


@pytest.fixture(scope="session")
def list_key():
    """The list to read. Must be the list's `name` or `id`, not its display name."""
    value = os.getenv("APPKIT_LIVE_LIST")
    if not value:
        pytest.skip("APPKIT_LIVE_LIST is not set (run `appkit-doctor` to see the keys)")
    return value


@pytest.fixture(scope="session")
def mail_recipient():
    """Opt-in: sending mail reaches a real person, so it is never implicit."""
    value = os.getenv("APPKIT_LIVE_MAIL_TO")
    if not value:
        pytest.skip("APPKIT_LIVE_MAIL_TO is not set (sending is opt-in)")
    if not os.getenv("APPKIT_MAIL_SENDER"):
        pytest.skip("APPKIT_MAIL_SENDER is not set")
    return value


@pytest.fixture(scope="session")
def database():
    """Reach the database once, so an unreachable one fails fast and clearly.

    Without this pre-flight every database test waits out the pool timeout on
    its own, and a job that is merely misconfigured looks like a hung one.
    Session scope means the failure is raised once and replayed to the rest.
    """
    if not os.getenv("APPKIT_DB_DSN"):
        pytest.skip("APPKIT_DB_DSN is not set")

    from appkit import db

    try:
        db.query("select 1")
    except Exception as exc:
        db._reset_pool()
        pytest.fail(
            f"APPKIT_DB_DSN is set but the database did not answer: "
            f"{type(exc).__name__}: {exc}\n"
            f"Check the host, the firewall/private endpoint, and that this "
            f"identity has an AAD role on the server. "
            f"APPKIT_DB_POOL_TIMEOUT shortens the wait.",
            pytrace=False,
        )
    return os.environ["APPKIT_DB_DSN"]


@pytest.fixture
def scratch_table(database):
    """A uniquely-named table, dropped afterwards whatever happens.

    Live tests share a database with whatever else lives there, so they never
    touch a name an application might own.
    """
    from appkit import db

    name = f"appkit_live_{uuid.uuid4().hex[:10]}"
    db.execute(f"create table {name} (id integer primary key, body text)")
    try:
        yield name
    finally:
        db.execute(f"drop table if exists {name}")
