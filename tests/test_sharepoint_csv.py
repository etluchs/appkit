"""The fake backend seeded from a SharePoint 'Export to CSV' file.

That export is not a plain CSV: it opens with a ``ListSchema=`` JSON preamble
and the CSV header row follows it on the *same* line. The helper below
reproduces that shape so the tests exercise the real thing.
"""

import csv
import io
import json

import pytest

import appkit
from appkit import _fake, sharepoint


def write_export(path, columns, rows, *, schema=True, bom=True):
    """Write a SharePoint CSV export.

    Args:
        columns: ``(internal name, SharePoint field type)`` pairs.
        rows: raw cell strings, one list per item.
        schema: include the ``ListSchema=`` preamble (a plain CSV without one
            is the other thing SharePoint can hand you).
        bom: write the UTF-8 BOM that SharePoint puts on the file.
    """
    body = io.StringIO()
    writer = csv.writer(body, quoting=csv.QUOTE_ALL, lineterminator="\n")
    writer.writerow([name for name, _ in columns])
    writer.writerows(rows)

    preamble = ""
    if schema:
        fields = [
            f'<Field ID="{{0000000{i}-0000-0000-0000-000000000000}}" Type="{field_type}" '
            f'Name="{name}" DisplayName="{name}" />'
            for i, (name, field_type) in enumerate(columns)
        ]
        # Note: no separator — the header row starts right after the JSON.
        preamble = "ListSchema=" + json.dumps({"schemaXmlList": fields})

    path.write_text(preamble + body.getvalue(), encoding="utf-8-sig" if bom else "utf-8")
    return path


@pytest.fixture
def export(tmp_path):
    """A stand-in for an export of a small list."""
    return write_export(
        tmp_path / "Services.csv",
        [
            ("ID", "Counter"),
            ("Title", "Text"),
            ("owner", "UserMulti"),
            ("user", "MultiChoice"),
            ("kosten", "Note"),
        ],
        [
            ["10", "Confluence", "amelia.stucki@uzh.ch", '["Mitarbeitende"]', "kostenlos"],
            ["11", "Jira", "ben.marti@uzh.ch", '["Mitarbeitende","Externe"]', ""],
        ],
    )


def test_the_schema_preamble_is_not_mistaken_for_data(export):
    _fake.load_sharepoint_export(export)
    rows = sharepoint.list_rows("Services")
    assert [r["Title"] for r in rows] == ["Confluence", "Jira"]
    assert not any("ListSchema" in key for row in rows for key in row)


def test_rows_are_keyed_by_the_lists_internal_field_names(export):
    _fake.load_sharepoint_export(export)
    assert set(sharepoint.list_rows("Services")[0]) == {
        "id", "Title", "owner", "user", "kosten",
    }


def test_multichoice_columns_become_lists(export):
    _fake.load_sharepoint_export(export)
    assert [r["user"] for r in sharepoint.list_rows("Services")] == [
        ["Mitarbeitende"],
        ["Mitarbeitende", "Externe"],
    ]


def test_a_text_column_that_looks_like_json_stays_text(tmp_path):
    """Only the schema decides what is multi-value — never the value's shape."""
    path = write_export(
        tmp_path / "Notes.csv",
        [("Title", "Text"), ("body", "Note")],
        [["One", '["not","a","choice"]']],
    )
    _fake.load_sharepoint_export(path)
    assert sharepoint.list_rows("Notes")[0]["body"] == '["not","a","choice"]'


def test_multiline_note_fields_survive(tmp_path):
    path = write_export(
        tmp_path / "Docs.csv",
        [("Title", "Text"), ("dokumentation", "Note")],
        [["Signing", "Merkblatt: https://t.uzh.ch/1mX\nFAQ: https://t.uzh.ch/1gY"]],
    )
    _fake.load_sharepoint_export(path)
    rows = sharepoint.list_rows("Docs")
    assert len(rows) == 1
    assert rows[0]["dokumentation"].splitlines() == [
        "Merkblatt: https://t.uzh.ch/1mX",
        "FAQ: https://t.uzh.ch/1gY",
    ]


def test_html_entities_are_unescaped(tmp_path):
    path = write_export(
        tmp_path / "Docs.csv",
        [("Title", "Text")],
        [["FAQ&#39;s &amp; more"]],
    )
    _fake.load_sharepoint_export(path)
    assert sharepoint.list_rows("Docs")[0]["Title"] == "FAQ's & more"


def test_empty_cells_are_dropped(export):
    _fake.load_sharepoint_export(export)
    jira = sharepoint.list_rows("Services")[1]
    assert "kosten" not in jira


def test_the_id_column_becomes_a_string_id(export):
    _fake.load_sharepoint_export(export)
    rows = sharepoint.list_rows("Services")
    assert [r["id"] for r in rows] == ["10", "11"]
    assert "ID" not in rows[0]


def test_ids_are_synthesised_when_the_export_has_no_id_column(tmp_path):
    path = write_export(tmp_path / "Notes.csv", [("Title", "Text")], [["One"], ["Two"]])
    _fake.load_sharepoint_export(path)
    assert [r["id"] for r in sharepoint.list_rows("Notes")] == ["1", "2"]


def test_a_file_without_the_bom_reads_the_same(tmp_path):
    path = write_export(
        tmp_path / "Plain.csv", [("Title", "Text")], [["One"]], bom=False
    )
    _fake.load_sharepoint_export(path)
    assert sharepoint.list_rows("Plain") == [{"Title": "One", "id": "1"}]


def test_an_ordinary_csv_without_a_schema_is_read_as_well(tmp_path):
    path = write_export(
        tmp_path / "Plain.csv",
        [("Title", "Text"), ("Status", "Choice")],
        [["One", "Open"]],
        schema=False,
    )
    _fake.load_sharepoint_export(path)
    assert sharepoint.list_rows("Plain") == [{"Title": "One", "Status": "Open", "id": "1"}]


def test_loaded_rows_work_with_the_select_projection(export):
    _fake.load_sharepoint_export(export)
    rows = sharepoint.list_rows("Services", select=["Title"])
    assert set(rows[0]) == {"id", "Title"}


# --- bad input -------------------------------------------------------------

def test_an_empty_file_is_an_error(tmp_path):
    path = tmp_path / "Empty.csv"
    path.write_text("", encoding="utf-8-sig")
    with pytest.raises(RuntimeError, match="no header row"):
        _fake.load_sharepoint_export(path)


def test_a_broken_schema_preamble_is_an_error(tmp_path):
    path = tmp_path / "Broken.csv"
    path.write_text('ListSchema={"schemaXmlList":[ "Title"\n"One"\n', encoding="utf-8-sig")
    with pytest.raises(RuntimeError, match="not valid JSON"):
        _fake.load_sharepoint_export(path)


def test_a_multichoice_value_that_is_not_a_json_array_is_an_error(tmp_path):
    path = write_export(
        tmp_path / "Services.csv",
        [("Title", "Text"), ("user", "MultiChoice")],
        [["One", "Mitarbeitende"]],
    )
    with pytest.raises(RuntimeError, match="user.*MultiChoice"):
        _fake.load_sharepoint_export(path)


def test_an_unsupported_file_type_is_an_error(tmp_path):
    path = tmp_path / "Requests.json"
    path.write_text("[]")
    with pytest.raises(RuntimeError, match="unsupported export type"):
        _fake.load_sharepoint_export(path)


# --- APPKIT_SHAREPOINT_FAKE_DIR -------------------------------------------

def test_csv_exports_are_picked_up_from_the_dir(export, monkeypatch):
    monkeypatch.setenv("APPKIT_SHAREPOINT_FAKE_DIR", str(export.parent))
    appkit.reset_fakes()

    assert len(sharepoint.list_rows("Services")) == 2
    assert len(sharepoint.list_rows("Requests")) == 4  # the built-in seed survives


def test_csv_and_xlsx_exports_can_share_a_dir(export, monkeypatch):
    import openpyxl

    workbook = openpyxl.Workbook()
    workbook.active.append(["Title"])
    workbook.active.append(["From Excel"])
    workbook.save(export.parent / "Requests.xlsx")

    monkeypatch.setenv("APPKIT_SHAREPOINT_FAKE_DIR", str(export.parent))
    appkit.reset_fakes()

    assert len(sharepoint.list_rows("Services")) == 2
    assert sharepoint.list_rows("Requests") == [{"Title": "From Excel", "id": "1"}]


def test_two_exports_for_the_same_list_are_an_error(export, monkeypatch):
    import openpyxl

    workbook = openpyxl.Workbook()
    workbook.active.append(["Title"])
    workbook.save(export.parent / "Services.xlsx")

    monkeypatch.setenv("APPKIT_SHAREPOINT_FAKE_DIR", str(export.parent))
    try:
        with pytest.raises(RuntimeError, match="more than one export for the 'Services'"):
            appkit.reset_fakes()
    finally:
        monkeypatch.delenv("APPKIT_SHAREPOINT_FAKE_DIR")


def test_dir_exports_survive_repeated_resets(export, monkeypatch):
    monkeypatch.setenv("APPKIT_SHAREPOINT_FAKE_DIR", str(export.parent))
    appkit.reset_fakes()
    appkit.reset_fakes()

    assert len(sharepoint.list_rows("Services")) == 2
