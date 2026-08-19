"""The fake backend seeded from a SharePoint 'Export to Excel' workbook."""

import datetime as dt

import pytest

import appkit
from appkit import _fake, sharepoint


def write_xlsx(path, rows):
    """Write ``rows`` (first row = header) to ``path`` as a workbook."""
    import openpyxl

    workbook = openpyxl.Workbook()
    for row in rows:
        workbook.active.append(row)
    workbook.save(path)
    return path


@pytest.fixture
def export(tmp_path):
    """A stand-in for what SharePoint exports for a small list."""
    return write_xlsx(
        tmp_path / "Approvals.xlsx",
        [
            ["ID", "Title", "Requester", "Amount", "Submitted"],
            [7, "New monitor", "amelia.stucki@uzh.ch", 249.9, dt.datetime(2026, 7, 20)],
            [8, "Ergonomic chair", "ben.marti@uzh.ch", 480, dt.datetime(2026, 7, 21)],
        ],
    )


def test_load_names_the_list_after_the_file(export):
    _fake.load_sharepoint_export(export)
    rows = sharepoint.list_rows("Approvals")
    assert [r["Title"] for r in rows] == ["New monitor", "Ergonomic chair"]


def test_load_accepts_an_explicit_list_name(export):
    _fake.load_sharepoint_export(export, list_name="Requests")
    assert [r["Title"] for r in sharepoint.list_rows("Requests")] == [
        "New monitor",
        "Ergonomic chair",
    ]


def test_ids_are_strings_like_the_graph_backend(export):
    _fake.load_sharepoint_export(export)
    assert [r["id"] for r in sharepoint.list_rows("Approvals")] == ["7", "8"]
    assert "ID" not in sharepoint.list_rows("Approvals")[0]


def test_ids_are_synthesised_when_the_export_has_no_id_column(tmp_path):
    path = write_xlsx(tmp_path / "Notes.xlsx", [["Title"], ["One"], ["Two"]])
    _fake.load_sharepoint_export(path)
    assert [r["id"] for r in sharepoint.list_rows("Notes")] == ["1", "2"]


def test_dates_become_iso_strings_and_numbers_stay_numbers(export):
    _fake.load_sharepoint_export(export)
    row = sharepoint.list_rows("Approvals")[0]
    assert row["Submitted"] == "2026-07-20"
    assert row["Amount"] == 249.9


def test_timestamps_keep_their_time_component(tmp_path):
    path = write_xlsx(
        tmp_path / "Events.xlsx",
        [["Title", "When"], ["Kickoff", dt.datetime(2026, 7, 20, 14, 30)]],
    )
    _fake.load_sharepoint_export(path)
    assert sharepoint.list_rows("Events")[0]["When"] == "2026-07-20T14:30:00"


def test_blank_rows_and_empty_cells_are_dropped(tmp_path):
    path = write_xlsx(
        tmp_path / "Sparse.xlsx",
        [
            ["Title", "Status"],
            ["Has status", "Open"],
            [None, None],
            ["  Padded  ", "   "],
        ],
    )
    _fake.load_sharepoint_export(path)
    rows = sharepoint.list_rows("Sparse")
    assert rows == [
        {"Title": "Has status", "Status": "Open", "id": "1"},
        {"Title": "Padded", "id": "2"},
    ]


def test_loaded_rows_work_with_the_select_projection(export):
    _fake.load_sharepoint_export(export)
    rows = sharepoint.list_rows("Approvals", select=["Title"])
    assert set(rows[0]) == {"id", "Title"}


def test_a_workbook_with_no_header_is_an_error(tmp_path):
    path = write_xlsx(tmp_path / "Empty.xlsx", [])
    with pytest.raises(RuntimeError, match="no header row"):
        _fake.load_sharepoint_export(path)


# --- APPKIT_SHAREPOINT_FAKE_DIR -------------------------------------------

def test_dir_is_loaded_on_reset_and_leaves_other_lists_seeded(export, monkeypatch):
    monkeypatch.setenv("APPKIT_SHAREPOINT_FAKE_DIR", str(export.parent))
    appkit.reset_fakes()

    assert len(sharepoint.list_rows("Approvals")) == 2
    assert len(sharepoint.list_rows("Requests")) == 4  # the built-in seed survives


def test_dir_export_replaces_a_seeded_list_of_the_same_name(tmp_path, monkeypatch):
    write_xlsx(tmp_path / "Requests.xlsx", [["Title"], ["The only one"]])
    monkeypatch.setenv("APPKIT_SHAREPOINT_FAKE_DIR", str(tmp_path))
    appkit.reset_fakes()

    assert sharepoint.list_rows("Requests") == [{"Title": "The only one", "id": "1"}]


def test_dir_survives_repeated_resets(export, monkeypatch):
    monkeypatch.setenv("APPKIT_SHAREPOINT_FAKE_DIR", str(export.parent))
    appkit.reset_fakes()
    appkit.reset_fakes()

    assert len(sharepoint.list_rows("Approvals")) == 2


def test_excel_lock_files_are_ignored(export, monkeypatch):
    (export.parent / "~$Approvals.xlsx").write_bytes(b"not a workbook")
    monkeypatch.setenv("APPKIT_SHAREPOINT_FAKE_DIR", str(export.parent))
    appkit.reset_fakes()

    assert len(sharepoint.list_rows("Approvals")) == 2


def test_a_missing_dir_is_reported_clearly(tmp_path, monkeypatch):
    monkeypatch.setenv("APPKIT_SHAREPOINT_FAKE_DIR", str(tmp_path / "nope"))
    try:
        with pytest.raises(RuntimeError, match="APPKIT_SHAREPOINT_FAKE_DIR"):
            appkit.reset_fakes()
    finally:
        monkeypatch.delenv("APPKIT_SHAREPOINT_FAKE_DIR")
