"""Postgres access with a connection pool and dict rows.

Public surface::

    from appkit import db
    db.execute("create table if not exists note (id serial primary key, body text)")
    db.execute("insert into note (body) values (%s)", ["hello"])
    rows = db.query("select * from note where body = %s", ["hello"])
    rows[0]["body"]  # -> "hello"

Rows are always returned as ``dict``s keyed by column name. Parameters use the
``%s`` placeholder style in both backends.

* ``azure`` mode uses a :class:`psycopg_pool.ConnectionPool` and authenticates
  to Azure Database for PostgreSQL with an AAD access token (the app's managed
  identity) as the password.
* ``fake`` mode uses a process-local, in-memory SQLite database exposing the
  same ``query`` / ``execute`` / ``transaction`` surface, so tests and local
  dev need no running Postgres.
"""

from __future__ import annotations

import contextlib
import sqlite3
import threading
from collections.abc import Iterator, Sequence
from typing import Any

from .config import env, is_fake

Params = Sequence[Any] | None


# --------------------------------------------------------------------------
# Public API
# --------------------------------------------------------------------------

def query(sql: str, params: Params = None) -> list[dict[str, Any]]:
    """Run ``sql`` and return all rows as dicts."""
    if is_fake():
        return _FakeDB.instance().query(sql, params)
    return _pg_query(sql, params)


def execute(sql: str, params: Params = None) -> int:
    """Run a statement that returns no rows; return the affected row count."""
    if is_fake():
        return _FakeDB.instance().execute(sql, params)
    return _pg_execute(sql, params)


@contextlib.contextmanager
def transaction() -> Iterator[Session]:
    """Context manager yielding a :class:`Session` bound to one transaction.

    Commits on clean exit, rolls back on exception.
    """
    if is_fake():
        with _FakeDB.instance().transaction() as session:
            yield session
    else:
        with _pg_transaction() as session:
            yield session


class Session:
    """A minimal query/execute surface bound to a single connection."""

    def __init__(self, execute_fn, query_fn) -> None:
        self._execute = execute_fn
        self._query = query_fn

    def query(self, sql: str, params: Params = None) -> list[dict[str, Any]]:
        return self._query(sql, params)

    def execute(self, sql: str, params: Params = None) -> int:
        return self._execute(sql, params)


# --------------------------------------------------------------------------
# Azure / psycopg backend
# --------------------------------------------------------------------------

PG_AAD_SCOPE = "https://ossrdbms-aad.database.windows.net/.default"

_pool_lock = threading.Lock()
_pool_instance = None


def _pg_password() -> str:
    """The password used for a *new* Postgres connection.

    Azure Database for PostgreSQL accepts an AAD access token as the password.
    Those tokens expire after about an hour, so this must be called for every
    connection the pool opens rather than once when the pool is built – a pool
    that captured a single token would authenticate fine for an hour and then be
    unable to open or replace any connection. ``_credential.token`` is cheap:
    azure-identity caches the token and refreshes it as it nears expiry.

    Tests override this seam to run the same psycopg code path against a plain
    password-authenticated Postgres.
    """
    from ._credential import token

    return token(PG_AAD_SCOPE)


def _connection_class():
    """A psycopg ``Connection`` that fetches a fresh password per connect."""
    import psycopg

    class _TokenConnection(psycopg.Connection):
        @classmethod
        def connect(cls, conninfo="", **kwargs):
            kwargs["password"] = _pg_password()
            return super().connect(conninfo, **kwargs)

    return _TokenConnection


def _pool():
    """Build (once) a psycopg connection pool that returns dict rows."""
    global _pool_instance

    with _pool_lock:
        if _pool_instance is not None:
            return _pool_instance

        from psycopg.rows import dict_row
        from psycopg_pool import ConnectionPool

        _pool_instance = ConnectionPool(
            conninfo=env("APPKIT_DB_DSN", required=True),
            connection_class=_connection_class(),
            kwargs={"row_factory": dict_row},
            min_size=1,
            max_size=int(env("APPKIT_DB_POOL_MAX", "10")),
            # How long a caller waits for a connection before giving up. The
            # default matches psycopg_pool's; lower it when a request should
            # fail fast rather than queue behind an unreachable server.
            timeout=float(env("APPKIT_DB_POOL_TIMEOUT", "30") or 30),
            check=ConnectionPool.check_connection,
            open=True,
        )
        return _pool_instance


def _reset_pool() -> None:
    """Close and drop the pool (used by tests and by config reloads)."""
    global _pool_instance

    with _pool_lock:
        if _pool_instance is not None:
            _pool_instance.close()
        _pool_instance = None


def _pg_query(sql: str, params: Params) -> list[dict[str, Any]]:
    with _pool().connection() as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        return list(cur.fetchall())


def _pg_execute(sql: str, params: Params) -> int:
    with _pool().connection() as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        return cur.rowcount


@contextlib.contextmanager
def _pg_transaction() -> Iterator[Session]:
    with _pool().connection() as conn:
        def q(sql: str, params: Params = None) -> list[dict[str, Any]]:
            with conn.cursor() as cur:
                cur.execute(sql, params)
                return list(cur.fetchall())

        def e(sql: str, params: Params = None) -> int:
            with conn.cursor() as cur:
                cur.execute(sql, params)
                return cur.rowcount

        with conn.transaction():
            yield Session(e, q)


# --------------------------------------------------------------------------
# Fake / SQLite backend
# --------------------------------------------------------------------------

class _FakeDB:
    """Process-local in-memory SQLite standing in for Postgres."""

    _lock = threading.RLock()
    _instance: _FakeDB | None = None

    def __init__(self) -> None:
        self._conn = sqlite3.connect(":memory:", check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._guard = threading.RLock()

    @classmethod
    def instance(cls) -> _FakeDB:
        with cls._lock:
            if cls._instance is None:
                cls._instance = cls()
            return cls._instance

    @classmethod
    def reset(cls) -> None:
        with cls._lock:
            if cls._instance is not None:
                cls._instance._conn.close()
            cls._instance = None

    @staticmethod
    def _translate(sql: str) -> str:
        # psycopg uses %s placeholders; sqlite uses ?.
        return sql.replace("%s", "?")

    def query(self, sql: str, params: Params) -> list[dict[str, Any]]:
        with self._guard:
            cur = self._conn.execute(self._translate(sql), tuple(params or ()))
            rows = [dict(row) for row in cur.fetchall()]
            cur.close()
            return rows

    def execute(self, sql: str, params: Params) -> int:
        with self._guard:
            cur = self._conn.execute(self._translate(sql), tuple(params or ()))
            self._conn.commit()
            count = cur.rowcount
            cur.close()
            return count

    @contextlib.contextmanager
    def transaction(self) -> Iterator[Session]:
        with self._guard:
            try:
                yield Session(self._tx_execute, self._tx_query)
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise

    def _tx_query(self, sql: str, params: Params = None) -> list[dict[str, Any]]:
        cur = self._conn.execute(self._translate(sql), tuple(params or ()))
        rows = [dict(row) for row in cur.fetchall()]
        cur.close()
        return rows

    def _tx_execute(self, sql: str, params: Params = None) -> int:
        cur = self._conn.execute(self._translate(sql), tuple(params or ()))
        count = cur.rowcount
        cur.close()
        return count


def _reset_fake() -> None:
    """Drop the in-memory SQLite database (used by test fixtures)."""
    _FakeDB.reset()
