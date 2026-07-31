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
from functools import lru_cache
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


@lru_cache(maxsize=1)
def _pool():
    """Build (once) a psycopg connection pool that returns dict rows.

    The password is an AAD access token from the managed identity. Tokens are
    short-lived; ``reconnect_timeout`` lets the pool refresh broken connections.
    For very long-lived processes, recycle the pool periodically.
    """
    from psycopg.rows import dict_row
    from psycopg_pool import ConnectionPool

    from ._credential import token

    conninfo = env("APPKIT_DB_DSN", required=True)
    password = token(PG_AAD_SCOPE)

    def configure(conn):
        conn.row_factory = dict_row

    pool = ConnectionPool(
        conninfo=conninfo,
        kwargs={"password": password, "row_factory": dict_row},
        configure=configure,
        min_size=1,
        max_size=int(env("APPKIT_DB_POOL_MAX", "10")),
        open=True,
    )
    return pool


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
