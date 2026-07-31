from appkit import _fake, sharepoint


def test_list_rows_returns_seeded_rows():
    rows = sharepoint.list_rows("Requests")
    assert len(rows) == 4
    assert {r["Status"] for r in rows} == {"Open", "In progress", "Closed"}
    assert all("Title" in r and "id" in r for r in rows)


def test_list_rows_unknown_list_is_empty():
    assert sharepoint.list_rows("DoesNotExist") == []


def test_list_rows_select_projects_fields():
    rows = sharepoint.list_rows("Requests", select=["Title", "Status"])
    assert rows
    assert set(rows[0]) == {"id", "Title", "Status"}


def test_list_rows_reflects_seeded_state():
    _fake.set_sharepoint_rows("Approvals", [{"id": "9", "Title": "One"}])
    rows = sharepoint.list_rows("Approvals")
    assert rows == [{"id": "9", "Title": "One"}]


def test_rows_are_copies_not_shared_state():
    rows = sharepoint.list_rows("Requests")
    rows[0]["Title"] = "mutated"
    assert sharepoint.list_rows("Requests")[0]["Title"] != "mutated"
