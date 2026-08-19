"""Read a SharePoint **Export to Excel** workbook into plain row dicts.

The first worksheet is used: its first non-empty row is the header, and every
row below it is one list item. See :mod:`appkit._export` for the dispatch and
the normalisation shared with the CSV reader.
"""

from __future__ import annotations

import datetime as _dt
from pathlib import Path
from typing import Any

from ._export import is_blank


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
            if all(is_blank(cell) for cell in cells):
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

    return rows


def _row(header: list[str | None], cells: tuple[Any, ...]) -> dict[str, Any]:
    """Pair one worksheet row with the header, dropping unnamed and empty cells."""
    row: dict[str, Any] = {}
    for name, cell in zip(header, cells, strict=False):
        value = _coerce(cell)
        if name and not is_blank(value):
            row[name] = value
    return row


def _header(cell: Any) -> str | None:
    return str(cell).strip() if not is_blank(cell) else None


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
