"""Azure Database for PostgreSQL, reached with an Entra token as the password.

The conformance suite already proves appkit's SQL behaviour against a real
Postgres. What only Azure can prove is the authentication path: that a token
from the managed identity is accepted as a password, that the pool asks for a
fresh one per connection, and that TLS and the Azure networking in between
actually work.
"""

import pytest

from appkit import db

pytestmark = pytest.mark.live


def test_the_token_is_accepted_as_a_password(database):
    row = db.query("select version() as version")[0]

    assert "PostgreSQL" in row["version"]


def test_the_connection_is_encrypted(database):
    """Azure requires TLS; a plaintext connection here would be a misconfiguration."""
    rows = db.query("select ssl from pg_stat_ssl where pid = pg_backend_pid()")

    assert rows and rows[0]["ssl"] is True


def test_the_identity_is_the_one_we_expect(database):
    row = db.query("select current_user as who, current_database() as db")[0]

    assert row["who"], "connected as nobody?"
    assert row["db"]


def test_read_write_round_trip(scratch_table):
    db.execute(f"insert into {scratch_table} (id, body) values (%s, %s)", [1, "hello"])

    rows = db.query(f"select body from {scratch_table} where id = %s", [1])

    assert rows == [{"body": "hello"}]


def test_transaction_rolls_back(scratch_table):
    db.execute(f"insert into {scratch_table} (id, body) values (%s, %s)", [1, "keep"])

    with pytest.raises(RuntimeError), db.transaction() as tx:
        tx.execute(f"insert into {scratch_table} (id, body) values (%s, %s)", [2, "drop"])
        raise RuntimeError("boom")

    assert len(db.query(f"select id from {scratch_table}")) == 1


def test_every_connection_gets_its_own_freshly_fetched_token(database, monkeypatch):
    """The pool must not capture one token for its lifetime.

    Azure's tokens expire after about an hour; a pool that reused one could not
    open or replace a connection afterwards. This proves the token is fetched
    per connect. That it is a *different* token after expiry is what
    ``test_soak.py`` proves.
    """
    real = db._pg_password
    passwords = []

    def counting():
        value = real()
        passwords.append(value)
        return value

    monkeypatch.setattr(db, "_pg_password", counting)
    db._reset_pool()

    pool = db._pool()
    pool.wait(timeout=30)
    assert len(passwords) == 1

    with pool.connection(), pool.connection():  # forces the pool to grow
        pass

    assert len(passwords) == 2, "the second connection reused the first one's token"
    db._reset_pool()
