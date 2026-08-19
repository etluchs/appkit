"""Read a SharePoint list export into plain row dicts.

SharePoint can hand you the contents of a list in two shapes, and the fake
backend accepts both as a stand-in for Microsoft Graph:

* ``.xlsx`` – **Export to Excel** (see :mod:`appkit._excel`)
* ``.csv``  – **Export to CSV**, which prefixes the data with the list's field
              schema (see :mod:`appkit._csv`)

This module owns the dispatch between them and the normalisation they share,
so that :mod:`appkit._fake` stays a store of state.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

#: Export file types the fake backend understands, lower-cased.
SUFFIXES = (".xlsx", ".csv")


def read_rows(path: str | Path) -> list[dict[str, Any]]:
    """Return the rows of the export at ``path`` as field dicts.

    Raises:
        RuntimeError: if the file type is unsupported or the export is unreadable.
    """
    path = Path(path)
    suffix = path.suffix.lower()

    if suffix == ".xlsx":
        from . import _excel as reader
    elif suffix == ".csv":
        from . import _csv as reader
    else:
        raise RuntimeError(
            f"{path}: unsupported export type '{path.suffix}'. "
            f"Expected one of: {', '.join(SUFFIXES)}."
        )

    rows = reader.read_rows(path)
    assign_ids(rows)
    return rows


def assign_ids(rows: list[dict[str, Any]]) -> None:
    """Normalise the export's ``ID`` column to a string ``id``, or number the rows.

    Graph always gives a list item a string id, so rows coming from an export
    have to carry one too.
    """
    for position, row in enumerate(rows, start=1):
        column = next((name for name in row if name.lower() == "id"), None)
        value = row.pop(column) if column else None
        row["id"] = str(value) if value is not None else str(position)


def is_blank(value: Any) -> bool:
    """True for cells Graph would have omitted from the ``fields`` facet."""
    return value is None or (isinstance(value, str) and not value.strip())
