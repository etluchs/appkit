"""In-memory state shared by the fake backends.

Everything here is process-local and thread-guarded. It exists so that local
development and tests behave like the real thing without touching Azure:

* SharePoint lists are dictionaries of rows.
* Sent mail lands in an inspectable outbox instead of a real mailbox.

Tests should call :func:`reset` (see ``appkit.reset_fakes``) between cases to
get a clean, deterministically-seeded world.
"""

from __future__ import annotations

import threading
from copy import deepcopy

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
    """Reset all fake state back to the deterministic seed."""
    with _lock:
        _sharepoint.clear()
        _sharepoint.update(_default_sharepoint())
        _outbox.clear()


# --- sharepoint ------------------------------------------------------------

def sharepoint_rows(list_name: str) -> list[dict]:
    with _lock:
        return deepcopy(_sharepoint.get(list_name, []))


def set_sharepoint_rows(list_name: str, rows: list[dict]) -> None:
    with _lock:
        _sharepoint[list_name] = deepcopy(rows)


# --- mail ------------------------------------------------------------------

def record_mail(message: dict) -> None:
    with _lock:
        _outbox.append(deepcopy(message))


def outbox() -> list[dict]:
    with _lock:
        return deepcopy(_outbox)


# Seed on import so `import appkit` yields a usable world immediately.
reset()
