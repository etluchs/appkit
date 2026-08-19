"""In-memory state shared by the fake backends.

Everything here is process-local and thread-guarded. It exists so that local
development and tests behave like the real thing without touching Azure:

* SharePoint lists are dictionaries of rows. They start from a small seed and
  can be replaced with real data by exporting the list from SharePoint
  (**Export to Excel**) – see :func:`load_sharepoint_xlsx` and
  ``APPKIT_SHAREPOINT_FAKE_DIR``.
* Sent mail lands in an inspectable outbox instead of a real mailbox.

Tests should call :func:`reset` (see ``appkit.reset_fakes``) between cases to
get a clean, deterministically-seeded world.
"""

from __future__ import annotations

import os
import threading
from copy import deepcopy
from pathlib import Path

_lock = threading.RLock()
_sharepoint: dict[str, list[dict]] = {}
_outbox: list[dict] = []


# --- seed data -------------------------------------------------------------

def _default_sharepoint() -> dict[str, list[dict]]:
    """A small, realistic SharePoint list the example app can render."""
    return {
        "Requests": [
            {
                "id": "1",
                "Title": "New laptop for onboarding",
                "Requester": "amelia.stucki@uzh.ch",
                "Department": "Finance",
                "Status": "Open",
                "Submitted": "2026-07-20",
            },
            {
                "id": "2",
                "Title": "Software licence renewal",
                "Requester": "ben.marti@uzh.ch",
                "Department": "IT",
                "Status": "In progress",
                "Submitted": "2026-07-22",
            },
            {
                "id": "3",
                "Title": "Conference travel approval",
                "Requester": "carla.rossi@uzh.ch",
                "Department": "Research",
                "Status": "Open",
                "Submitted": "2026-07-25",
            },
            {
                "id": "4",
                "Title": "Standing desk request",
                "Requester": "deniz.yilmaz@uzh.ch",
                "Department": "HR",
                "Status": "Closed",
                "Submitted": "2026-07-18",
            },
        ],
    }


def reset() -> None:
    """Reset all fake state back to the deterministic seed.

    Any ``.xlsx`` exports found in ``APPKIT_SHAREPOINT_FAKE_DIR`` are layered on
    top of the seed, so re-seeding between tests keeps them in place.
    """
    with _lock:
        _sharepoint.clear()
        _sharepoint.update(_default_sharepoint())
        _outbox.clear()
    _load_sharepoint_dir()


# --- sharepoint ------------------------------------------------------------

def sharepoint_rows(list_name: str) -> list[dict]:
    with _lock:
        return deepcopy(_sharepoint.get(list_name, []))


def set_sharepoint_rows(list_name: str, rows: list[dict]) -> None:
    with _lock:
        _sharepoint[list_name] = deepcopy(rows)


def load_sharepoint_xlsx(path: str | Path, list_name: str | None = None) -> list[dict]:
    """Seed a fake SharePoint list from an **Export to Excel** workbook.

    Args:
        path: The exported ``.xlsx`` file.
        list_name: List to populate. Defaults to the file name without its
            extension, so ``Requests.xlsx`` becomes the ``Requests`` list.

    Returns:
        The rows that were loaded.
    """
    from . import _excel

    path = Path(path)
    rows = _excel.read_rows(path)
    set_sharepoint_rows(list_name or path.stem, rows)
    return rows


def _load_sharepoint_dir() -> None:
    """Load every ``<ListName>.xlsx`` in ``APPKIT_SHAREPOINT_FAKE_DIR``, if set."""
    configured = os.getenv("APPKIT_SHAREPOINT_FAKE_DIR", "").strip()
    if not configured:
        return

    directory = Path(configured).expanduser()
    if not directory.is_dir():
        raise RuntimeError(
            f"APPKIT_SHAREPOINT_FAKE_DIR points at {directory}, which is not a directory."
        )

    # Excel writes ~$Name.xlsx lock files next to an open workbook; skip them.
    for file in sorted(directory.glob("*.xlsx")):
        if not file.name.startswith("~$"):
            load_sharepoint_xlsx(file)


# --- mail ------------------------------------------------------------------

def record_mail(message: dict) -> None:
    with _lock:
        _outbox.append(deepcopy(message))


def outbox() -> list[dict]:
    with _lock:
        return deepcopy(_outbox)


# Seed on import so `import appkit` yields a usable world immediately.
reset()
