from appkit import db


def _make_table():
    db.execute(
        "create table if not exists note (id integer primary key, body text)"
    )


def test_execute_and_query_roundtrip():
    _make_table()
    db.execute("insert into note (body) values (%s)", ["hello"])
    db.execute("insert into note (body) values (%s)", ["world"])
    rows = db.query("select body from note order by id")
    assert [r["body"] for r in rows] == ["hello", "world"]


def test_query_returns_dict_rows():
    _make_table()
    db.execute("insert into note (body) values (%s)", ["x"])
    row = db.query("select id, body from note")[0]
    assert isinstance(row, dict)
    assert set(row) == {"id", "body"}


def test_transaction_commits():
    _make_table()
    with db.transaction() as tx:
        tx.execute("insert into note (body) values (%s)", ["a"])
        tx.execute("insert into note (body) values (%s)", ["b"])
    assert len(db.query("select * from note")) == 2


def test_transaction_rolls_back_on_error():
    _make_table()
    db.execute("insert into note (body) values (%s)", ["keep"])
    try:
        with db.transaction() as tx:
            tx.execute("insert into note (body) values (%s)", ["drop"])
            raise RuntimeError("boom")
    except RuntimeError:
        pass
    bodies = [r["body"] for r in db.query("select body from note")]
    assert bodies == ["keep"]


def test_state_is_reset_between_tests():
    # The autouse fixture drops the in-memory DB, so the table is gone.
    rows = db.query(
        "select name from sqlite_master where type='table' and name='note'"
    )
    assert rows == []
