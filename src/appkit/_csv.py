"""Read a SharePoint **Export to CSV** file into plain row dicts.

The export is not quite a plain CSV. It opens with the list's field schema::

    ListSchema={"schemaXmlList":["<Field Name=... Type=... />", ...]}"Title","kosten",...
    "Confluence","kostenlos",...

The schema is a JSON object, and the CSV header row follows it *on the same
line*. The header holds the list's internal field names – exactly the keys
Graph puts in an item's ``fields`` facet – so rows are keyed by it directly;
the schema is consulted only for field *types*, which is what tells a
multi-choice column (a JSON array) apart from ordinary text.

A file without the ``ListSchema=`` prefix is read as an ordinary CSV whose
first row is the header.
"""

from __future__ import annotations

import csv
import html
import io
import json
from pathlib import Path
from typing import Any
from xml.etree import ElementTree

from ._export import is_blank

_PREFIX = "ListSchema="

#: Types whose exported value is a JSON array rather than a scalar.
_LIST_TYPES = frozenset({"MultiChoice"})


def read_rows(path: str | Path) -> list[dict[str, Any]]:
    """Return the rows of the export at ``path`` as field dicts."""
    path = Path(path)
    # SharePoint writes the file with a BOM; utf-8-sig strips it if present.
    types, body = _split_schema(path, path.read_text(encoding="utf-8-sig"))

    reader = csv.DictReader(io.StringIO(body))
    if reader.fieldnames is None:
        raise RuntimeError(f"{path}: no header row found")

    rows: list[dict[str, Any]] = []
    for record in reader:
        row = _row(path, record, types)
        if row:
            rows.append(row)
    return rows


def _split_schema(path: Path, text: str) -> tuple[dict[str, str], str]:
    """Split the ``ListSchema=`` preamble off, returning field types and the CSV."""
    if not text.startswith(_PREFIX):
        return {}, text  # an ordinary CSV export, header row first

    try:
        schema, end = json.JSONDecoder().raw_decode(text, len(_PREFIX))
    except ValueError as exc:
        raise RuntimeError(f"{path}: ListSchema is not valid JSON ({exc})") from exc
    return _field_types(path, schema), text[end:]


def _field_types(path: Path, schema: Any) -> dict[str, str]:
    """Map internal field name -> SharePoint field type from the schema XML."""
    types: dict[str, str] = {}
    for definition in schema.get("schemaXmlList", []):
        try:
            field = ElementTree.fromstring(definition)
        except ElementTree.ParseError as exc:
            raise RuntimeError(f"{path}: ListSchema holds invalid field XML ({exc})") from exc
        name = field.get("Name")
        if name:
            types[name] = field.get("Type", "")
    return types


def _row(path: Path, record: dict, types: dict[str, str]) -> dict[str, Any]:
    """Turn one CSV record into a field dict, dropping unnamed and empty cells."""
    row: dict[str, Any] = {}
    for name, cell in record.items():
        # DictReader files any surplus values under a None key; ignore them.
        if name is None or is_blank(cell):
            continue
        row[name] = _coerce(path, name, cell, types.get(name, ""))
    return row


def _coerce(path: Path, name: str, cell: str, field_type: str) -> Any:
    """Convert one exported cell to what Graph would have returned for that field.

    Values are left as text: the export carries no type information beyond the
    schema, and only multi-value columns have a machine-readable shape.
    """
    value = html.unescape(cell).strip()
    if field_type not in _LIST_TYPES:
        return value
    try:
        return json.loads(value)
    except ValueError as exc:
        raise RuntimeError(
            f"{path}: column '{name}' is a {field_type} field but its value is not "
            f"a JSON array ({exc})"
        ) from exc
