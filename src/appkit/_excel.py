"""Read a SharePoint list export (``.xlsx``) into plain row dicts.

This is the fake backend's stand-in for Microsoft Graph: instead of calling
``/sites/{site}/lists/{list}/items``, local dev can use the workbook that
SharePoint's own **Export to Excel** produces. Parsing lives here for the same
reason the Graph transport lives in :mod:`appkit._graph` – so that
:mod:`appkit._fake` stays a store of state.

The first worksheet is used. Its first non-empty row is the header, every row
below it is one list item, and the export's ``ID`` column (if present) becomes
the ``id`` key that the Graph path also guarantees.
"""

from __future__ import annotations

import datetime as _dt
from pathlib import Path
from typing import Any


def read_rows(path: str | Path) -> list[dict[str, Any]]:
    """Return the rows of ``path``'s first worksheet as field dicts.

    Raises:
        RuntimeError: if the workbook has no usable header row.
    """
    import openpyxl  # lazy: the azure backend never needs it

    path = Path(path)
    workbook = openpyxl.load_workbook(path, read_only=True, data_only=True)
    try:
        sheet = workbook.worksheets[0]
        header: list[str | None] | None = None
        rows: list[dict[str, Any]] = []

        for cells in sheet.iter_rows(values_only=True):
            if all(_is_blank(cell) for cell in cells):
                continue  # exports carry blank spacer and trailing rows
            if header is None:
                header = [_header(cell) for cell in cells]
                continue
            row = _row(header, cells)
            if row:
                rows.append(row)

        if header is None:
            raise RuntimeError(f"{path}: sheet '{sheet.title}' is empty (no header row)")
    finally:
        workbook.close()

    _assign_ids(rows)
    return rows


def _row(header: list[str | None], cells: tuple[Any, ...]) -> dict[str, Any]:
    """Pair one worksheet row with the header, dropping unnamed and empty cells."""
    row: dict[str, Any] = {}
    for name, cell in zip(header, cells, strict=False):
        value = _coerce(cell)
        if name and not _is_blank(value):
            row[name] = value
    return row


def _assign_ids(rows: list[dict[str, Any]]) -> None:
    """Normalise the export's ``ID`` column to a string ``id``, or number the rows."""
    for position, row in enumerate(rows, start=1):
        column = next((name for name in row if name.lower() == "id"), None)
        value = row.pop(column) if column else None
        row["id"] = str(value) if value is not None else str(position)


def _header(cell: Any) -> str | None:
    return str(cell).strip() if not _is_blank(cell) else None


def _coerce(cell: Any) -> Any:
    """Convert an Excel cell to what Graph would have returned for that field."""
    if isinstance(cell, _dt.datetime):
        # A date-only column round-trips through Excel as midnight.
        return cell.date().isoformat() if cell.time() == _dt.time.min else cell.isoformat()
    if isinstance(cell, _dt.date | _dt.time):
        return cell.isoformat()
    if isinstance(cell, str):
        return cell.strip()
    return cell


def _is_blank(value: Any) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())
