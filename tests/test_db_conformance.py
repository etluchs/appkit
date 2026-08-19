"""One database suite, run against both backends.

Every test here runs twice: once on the in-memory SQLite fake, once on a real
Postgres — through the same ``psycopg`` pool, ``Session`` and transaction code
production uses. Only the *password source* is swapped: production passes an
AAD access token, this suite passes the container's password. Everything above
that (pooling, dict rows, placeholders, transactions, dialect) is the real
thing.

The Postgres half is skipped unless ``APPKIT_TEST_PG_DSN`` is set::

    docker run -d --name appkit-pg -p 5432:5432 \
        -e POSTGRES_USER=appkit -e POSTGRES_PASSWORD=appkit \
        -e POSTGRES_DB=appkit postgres:16
    APPKIT_TEST_PG_DSN=postgresql://appkit@localhost:5432/appkit uv run pytest

Divergences that the fake cannot currently honour are marked with an explicit
``xfail`` rather than hidden: each one is a way an app that only ever ran
locally can break in Azure.
"""

import os

import pytest

from appkit import db

PG_DSN_ENV = "APPKIT_TEST_PG_DSN"


@pytest.fixture(params=["fake", "postgres"])
def backend(request, monkeypatch):
    """Run the test body against the fake and against real Postgres."""
    name = request.param
    if name == "fake":
        yield name  # the autouse fixture already selected the fake backend
        return

    dsn = os.getenv(PG_DSN_ENV)
    if not dsn:
        pytest.skip(f"set {PG_DSN_ENV} to run the Postgres conformance suite")

    monkeypatch.setenv("APPKIT_BACKEND", "azure")
    monkeypatch.setenv("APPKIT_DB_DSN", dsn)
    password = os.getenv("APPKIT_TEST_PG_PASSWORD", "appkit")
    monkeypatch.setattr(db, "_pg_password", lambda: password)

    db._reset_pool()
    _drop()
    yield name
    _drop()
    db._reset_pool()


def _drop():
    db.execute("drop table if exists note")


def _make_table():
    db.execute("create table note (id integer primary key, body text)")


def _insert(note_id, body):
    db.execute("insert into note (id, body) values (%s, %s)", [note_id, body])


# --------------------------------------------------------------------------
# Behaviour both backends must agree on
# --------------------------------------------------------------------------

def test_execute_and_query_roundtrip(backend):
    _make_table()
    _insert(1, "hello")
    _insert(2, "world")

    rows = db.query("select body from note order by id")
    assert [r["body"] for r in rows] == ["hello", "world"]


def test_rows_are_dicts_keyed_by_column(backend):
    _make_table()
    _insert(1, "x")

    row = db.query("select id, body from note")[0]
    assert isinstance(row, dict)
    assert row == {"id": 1, "body": "x"}


def test_placeholders_bind_rather_than_interpolate(backend):
    _make_table()
    _insert(1, "o'brien; drop table note--")

    rows = db.query("select body from note where body = %s", ["o'brien; drop table note--"])
    assert len(rows) == 1


def test_execute_reports_affected_rows(backend):
    _make_table()
    _insert(1, "a")
    _insert(2, "b")

    assert db.execute("update note set body = %s where id = %s", ["c", 1]) == 1
    assert db.execute("delete from note where id > %s", [0]) == 2


def test_query_with_no_matches_is_empty(backend):
    _make_table()
    assert db.query("select * from note where id = %s", [999]) == []


def test_nulls_round_trip(backend):
    _make_table()
    db.execute("insert into note (id, body) values (%s, %s)", [1, None])

    assert db.query("select body from note")[0]["body"] is None


def test_transaction_commits(backend):
    _make_table()
    with db.transaction() as tx:
        tx.execute("insert into note (id, body) values (%s, %s)", [1, "a"])
        tx.execute("insert into note (id, body) values (%s, %s)", [2, "b"])

    assert len(db.query("select * from note")) == 2


def test_transaction_rolls_back_on_error(backend):
    _make_table()
    _insert(1, "keep")

    with pytest.raises(RuntimeError), db.transaction() as tx:
        tx.execute("insert into note (id, body) values (%s, %s)", [2, "drop"])
        raise RuntimeError("boom")

    assert [r["body"] for r in db.query("select body from note")] == ["keep"]


def test_transaction_sees_its_own_writes(backend):
    _make_table()
    with db.transaction() as tx:
        tx.execute("insert into note (id, body) values (%s, %s)", [1, "a"])
        assert tx.query("select body from note") == [{"body": "a"}]


def test_pool_survives_sequential_use(backend):
    """Several calls in a row must not exhaust or wedge the pool."""
    _make_table()
    for i in range(1, 11):
        _insert(i, f"row-{i}")
    assert len(db.query("select id from note")) == 10


def test_a_fresh_password_is_fetched_for_every_connection(backend, monkeypatch):
    """Regression: the pool used to capture one AAD token for its lifetime.

    Those tokens expire after about an hour, after which the pool could no
    longer open or replace a connection — an app that worked all morning would
    start failing, and no fake test could ever show it.
    """
    if backend == "fake":
        pytest.skip("the connection pool only exists on the azure backend")

    calls = []
    password = os.getenv("APPKIT_TEST_PG_PASSWORD", "appkit")

    def counting_password():
        calls.append(1)
        return password

    monkeypatch.setattr(db, "_pg_password", counting_password)
    db._reset_pool()

    pool = db._pool()
    pool.wait(timeout=10)
    assert len(calls) == 1

    with pool.connection(), pool.connection():  # forces the pool to grow
        pass

    assert len(calls) == 2


# --------------------------------------------------------------------------
# Known divergences: these pass on Postgres and fail on the fake
# --------------------------------------------------------------------------

def test_percent_literal_in_sql(backend):
    """``%s`` -> ``?`` is a blind string replace in the fake backend.

    ``like '%stuff'`` becomes ``like '?tuff'``, silently matching nothing.
    """
    if backend == "fake":
        pytest.xfail("fake backend corrupts a literal '%' followed by 's'")

    _make_table()
    _insert(1, "stuff happens")
    _insert(2, "nothing here")

    rows = db.query("select body from note where body like '%stuff%'")
    assert len(rows) == 1


def test_serial_primary_key_autoincrements(backend):
    """The README's own example. On SQLite ``serial`` yields a NULL id."""
    if backend == "fake":
        pytest.xfail("SQLite has no `serial`; ids come back as None")

    db.execute("create table note (id serial primary key, body text)")
    db.execute("insert into note (body) values (%s)", ["hello"])

    assert db.query("select id from note")[0]["id"] == 1


def test_returning_clause(backend):
    if backend == "fake":
        pytest.xfail("sqlite3 in Python <3.13 does not support RETURNING via execute()")

    _make_table()
    rows = db.query("insert into note (id, body) values (%s, %s) returning id", [1, "a"])
    assert rows == [{"id": 1}]


def test_boolean_and_timestamp_types(backend):
    if backend == "fake":
        pytest.xfail("SQLite has no native boolean or timestamp type")

    db.execute("create table flag (id integer primary key, on_ boolean, at timestamptz)")
    db.execute("insert into flag (id, on_, at) values (%s, %s, now())", [1, True])

    row = db.query("select on_, at from flag")[0]
    assert row["on_"] is True
    assert hasattr(row["at"], "year")
    db.execute("drop table if exists flag")
