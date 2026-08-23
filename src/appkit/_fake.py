"""In-memory state shared by the fake backends.

Everything here is process-local and thread-guarded. It exists so that local
development and tests behave like the real thing without touching Azure:

* SharePoint lists are dictionaries of rows. They start from a small seed and
  can be replaced with real data by exporting the list from SharePoint
  (**Export to Excel** or **Export to CSV**) – see
  :func:`load_sharepoint_export` and ``APPKIT_SHAREPOINT_FAKE_DIR``.
* Sent mail lands in an inspectable outbox instead of a real mailbox.
* The directory is a small list of people, and the DNS is a set of names that
  already exist.

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
_people: list[dict] = []
_dns_names: set[str] = set()


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


def _default_people() -> list[dict]:
    """A small directory, shaped like the real one.

    ``employee_type`` carries the tenant's own vocabulary, not a tidied-up
    version of it: ``employees_only`` matches it with a ``*h*`` filter, so a
    seed reading "employee" would quietly match nobody.

    Two of these people — Nicole Hartmann and Peter Frei — deliberately share
    the ``it-support@uzh.ch`` mailbox while keeping distinct shortnames. That is
    the case that makes email unusable as an identity, and any app keying
    authorization on email will fail visibly against this seed rather than
    quietly in production.
    """
    return [
        {"shortname": "astucki", "display_name": "Amelia Stucki",
         "email": "amelia.stucki@uzh.ch", "id": "u-astucki", "employee_type": "hauptamtlich"},
        {"shortname": "bmarti", "display_name": "Ben Marti",
         "email": "ben.marti@uzh.ch", "id": "u-bmarti", "employee_type": "hauptamtlich"},
        {"shortname": "crossi", "display_name": "Carla Rossi",
         "email": "carla.rossi@uzh.ch", "id": "u-crossi", "employee_type": "hauptamtlich"},
        {"shortname": "arossi", "display_name": "Alessandra Rossi",
         "email": "alessandra.rossi@uzh.ch", "id": "u-arossi", "employee_type": "hauptamtlich"},
        {"shortname": "smeier", "display_name": "Sandra Meier",
         "email": "sandra.meier@uzh.ch", "id": "u-smeier", "employee_type": "hauptamtlich"},
        {"shortname": "dyilmaz", "display_name": "Deniz Yilmaz",
         "email": "deniz.yilmaz@uzh.ch", "id": "u-dyilmaz", "employee_type": "hauptamtlich"},
        {"shortname": "taeberli", "display_name": "Tina Aeberli",
         "email": "tina.aeberli@uzh.ch", "id": "u-taeberli", "employee_type": "hauptamtlich"},
        {"shortname": "ggantner", "display_name": "Giulia Tina Gantner",
         "email": "giulia.gantner@uzh.ch", "id": "u-ggantner", "employee_type": "hauptamtlich"},
        {"shortname": "tinadl", "display_name": "Tomas Indal",
         "email": "tomas.indal@uzh.ch", "id": "u-tinadl", "employee_type": "hauptamtlich"},
        {"shortname": "nhartma", "display_name": "Nicole Hartmann",
         "email": "it-support@uzh.ch", "id": "u-nhartma", "employee_type": "hauptamtlich"},
        {"shortname": "pfrei", "display_name": "Peter Frei",
         "email": "it-support@uzh.ch", "id": "u-pfrei", "employee_type": "hauptamtlich"},
        {"shortname": "lstud", "display_name": "Lara Studer",
         "email": "lara.studer@uzh.ch", "id": "u-lstud", "employee_type": "student"},
    ]


def _default_dns_names() -> set[str]:
    """Names a lookup should report as already taken."""
    return {"taken.azr.uzh.ch", "www.uzh.ch"}


def reset() -> None:
    """Reset all fake state back to the deterministic seed.

    Any list exports found in ``APPKIT_SHAREPOINT_FAKE_DIR`` are layered on top
    of the seed, so re-seeding between tests keeps them in place.
    """
    with _lock:
        _sharepoint.clear()
        _sharepoint.update(_default_sharepoint())
        _outbox.clear()
        _people.clear()
        _people.extend(_default_people())
        _dns_names.clear()
        _dns_names.update(_default_dns_names())
    _load_sharepoint_dir()


# --- sharepoint ------------------------------------------------------------

def sharepoint_rows(list_name: str) -> list[dict]:
    with _lock:
        return deepcopy(_sharepoint.get(list_name, []))


def set_sharepoint_rows(list_name: str, rows: list[dict]) -> None:
    with _lock:
        _sharepoint[list_name] = deepcopy(rows)


def load_sharepoint_export(path: str | Path, list_name: str | None = None) -> list[dict]:
    """Seed a fake SharePoint list from an exported ``.xlsx`` or ``.csv`` file.

    Args:
        path: The file produced by SharePoint's **Export to Excel** or
            **Export to CSV**.
        list_name: List to populate. Defaults to the file name without its
            extension, so ``Requests.csv`` becomes the ``Requests`` list.

    Returns:
        The rows that were loaded.
    """
    from . import _export

    path = Path(path)
    rows = _export.read_rows(path)
    set_sharepoint_rows(list_name or path.stem, rows)
    return rows


def _load_sharepoint_dir() -> None:
    """Load every list export in ``APPKIT_SHAREPOINT_FAKE_DIR``, if it is set."""
    from . import _export

    configured = os.getenv("APPKIT_SHAREPOINT_FAKE_DIR", "").strip()
    if not configured:
        return

    directory = Path(configured).expanduser()
    if not directory.is_dir():
        raise RuntimeError(
            f"APPKIT_SHAREPOINT_FAKE_DIR points at {directory}, which is not a directory."
        )

    exports: dict[str, list[Path]] = {}
    for file in sorted(directory.iterdir()):
        # Excel writes ~$Name.xlsx lock files next to an open workbook; skip them.
        if file.suffix.lower() in _export.SUFFIXES and not file.name.startswith("~$"):
            exports.setdefault(file.stem, []).append(file)

    for list_name, files in exports.items():
        if len(files) > 1:
            raise RuntimeError(
                f"{directory} holds more than one export for the '{list_name}' list: "
                f"{', '.join(f.name for f in files)}. Keep only one."
            )
        load_sharepoint_export(files[0], list_name)


# --- directory -------------------------------------------------------------

def people() -> list[dict]:
    with _lock:
        return deepcopy(_people)


def set_people(rows: list[dict]) -> None:
    """Replace the fake directory. Each row uses ``Person``'s field names."""
    with _lock:
        _people.clear()
        _people.extend(deepcopy(rows))


# --- dns -------------------------------------------------------------------

def dns_names() -> set[str]:
    with _lock:
        return set(_dns_names)


def set_dns_names(names: set[str]) -> None:
    """Replace the set of names the fake resolver reports as existing."""
    with _lock:
        _dns_names.clear()
        _dns_names.update(name.strip().lower().rstrip(".") for name in names)


# --- mail ------------------------------------------------------------------

def record_mail(message: dict) -> None:
    with _lock:
        _outbox.append(deepcopy(message))


def outbox() -> list[dict]:
    with _lock:
        return deepcopy(_outbox)


# Seed on import so `import appkit` yields a usable world immediately.
reset()
