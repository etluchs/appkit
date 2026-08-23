"""Look people up in the tenant directory via Microsoft Graph.

Public surface::

    from appkit import directory

    people = directory.search_people("nicole hartmann")
    person = directory.person_by_shortname("nhartma")

The identity that matters is :attr:`Person.shortname` — Entra's
``onPremisesSamAccountName``. Email is *not* an identity: shared mailboxes mean
two people can carry the same address, so an app that keys authorization on
email will grant one person another's permissions. Key on the shortname.

In ``fake`` mode people come from :mod:`appkit._fake`; in ``azure`` mode from
Graph, authenticated with the app's managed identity (needs ``User.Read.All``).

**One difference between the backends worth knowing.** Graph's ``$filter`` offers
``startsWith``, not arbitrary substring matching, so ``azure`` finds people by a
prefix of their display name, given name, surname, mail or shortname — searching
"essandra" will not find *Alessandra*. The fake matches substrings too, so it is
the more forgiving of the two. Relevance ordering is identical in both, because
it is computed here rather than by the server.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from .config import env, is_fake

MAX_SEARCH_TOKENS = 8
_TOKEN_SEPARATORS = re.compile(r"[\s,;]+")
_WORD_BOUNDARY = re.compile(r"[^0-9A-Za-z]+")

# The Graph fields a prefix search looks at, and the ones we project.
_SEARCH_FIELDS = ("displayName", "mail", "onPremisesSamAccountName", "givenName", "surname")
_SELECT = "id,displayName,mail,onPremisesSamAccountName,employeeType"


class SearchTermTooManyTermsError(ValueError):
    """Raised when a search term contains more than :data:`MAX_SEARCH_TOKENS` terms."""


@dataclass(frozen=True)
class Person:
    """One person in the directory."""

    shortname: str  # onPremisesSamAccountName — the authorization key
    display_name: str
    email: str = ""
    id: str = ""  # Entra object id (oid)
    employee_type: str = ""

    def __post_init__(self) -> None:
        # Some directories carry the login in the display name, as
        # "Nicole Hartmann (nhartma)". It is redundant once `shortname` holds
        # it, and it makes every chip and mail salutation read oddly.
        name = self.display_name
        if name.endswith(")") and "(" in name:
            stem, _, tail = name.rpartition("(")
            if tail[:-1].strip().casefold() == self.shortname.casefold():
                object.__setattr__(self, "display_name", stem.strip())


# --- relevance -------------------------------------------------------------
#
# Ported from the LDAP directory this module replaces. Neither LDAP nor Graph
# ranks matches, so entries are ordered here instead: searching "sandra" must
# offer "Sandra Meier" before "Alessandra Rossi" without dropping the latter.
# The two dimensions are match strength (exact > prefix > substring) and
# position (leading > inner word). Exact beats prefix, so "tina" prefers the
# name "Tina" over the login "tinadl"; within exact, a leading match beats an
# inner one, so the given name "Tina Aeberli" beats the middle name in
# "Giulia Tina Gantner".

RANK_LEADING_EXACT = 0
RANK_WORD_EXACT = 1
RANK_VALUE_PREFIX = 2
RANK_WORD_PREFIX = 3
RANK_SUBSTRING = 4
RANK_NO_MATCH = 5


def tokenize(search_term: str) -> list[str]:
    """Split a search term into the terms an entry must match.

    Separators are runs of whitespace, comma and semicolon, so surname-first
    input such as ``Hartmann, Nicole`` yields the same tokens as
    ``Nicole Hartmann``.

    Raises:
        ValueError: If the term yields no tokens.
        SearchTermTooManyTermsError: If it yields more than
            :data:`MAX_SEARCH_TOKENS` tokens.
    """
    tokens = [token for token in _TOKEN_SEPARATORS.split(search_term) if token]
    if not tokens:
        raise ValueError("Search term must contain at least one searchable term.")
    if len(tokens) > MAX_SEARCH_TOKENS:
        raise SearchTermTooManyTermsError(
            f"Search term must not contain more than {MAX_SEARCH_TOKENS} terms."
        )
    return tokens


def _token_rank(token: str, values: tuple[str, ...]) -> int:
    """Rate how prominently a token appears in an entry's searched attributes."""
    needle = token.casefold()
    if not needle:
        return RANK_NO_MATCH
    best = RANK_NO_MATCH
    for value in values:
        haystack = value.casefold()
        if not haystack:
            continue
        words = _WORD_BOUNDARY.split(haystack)
        if haystack == needle or (words and words[0] == needle):
            return RANK_LEADING_EXACT
        if needle in words:
            best = min(best, RANK_WORD_EXACT)
        elif haystack.startswith(needle):
            best = min(best, RANK_VALUE_PREFIX)
        elif needle in haystack:
            if any(word.startswith(needle) for word in words):
                best = min(best, RANK_WORD_PREFIX)
            else:
                best = min(best, RANK_SUBSTRING)
    return best


def _relevance_key(person: Person, tokens: list[str]) -> tuple[int, str]:
    """Sort key ordering results by relevance, then alphabetically.

    Per-token tiers are summed, so an entry matching every token prominently
    sorts ahead of one that matches some of them only incidentally.
    """
    values = (person.display_name, person.email, person.shortname)
    return (
        sum(_token_rank(token, values) for token in tokens),
        person.display_name.casefold(),
    )


# --- public API ------------------------------------------------------------

def search_people(
    term: str,
    *,
    employees_only: bool = False,
    exclude: Iterable[str] = (),
    limit: int = 50,
) -> list[Person]:
    """Search the directory, most relevant first.

    Args:
        term: What the user typed. Split on whitespace, comma and semicolon;
            every token must match.
        employees_only: Restrict to people whose ``employeeType`` marks them as
            employees, as the staff pickers do.
        exclude: Shortnames *or* email addresses to drop from the results,
            compared case-insensitively. Both are accepted because callers hold
            a mix of the two.
        limit: Maximum number of people to return.
    """
    tokens = tokenize(term)
    people = _fake_people(tokens) if is_fake() else _graph_people(tokens)

    # An entry with no shortname cannot be authorized against, and one with no
    # mailbox cannot be told anything. Offering either just moves the failure
    # to whoever picks them. The LDAP directory this replaces skipped them too.
    people = [person for person in people if person.shortname and person.email]

    if employees_only:
        people = [p for p in people if _is_employee(p)]

    dropped = {value.casefold() for value in exclude if value}
    if dropped:
        people = [
            p
            for p in people
            if p.shortname.casefold() not in dropped and p.email.casefold() not in dropped
        ]

    people.sort(key=lambda p: _relevance_key(p, tokens))
    return people[:limit]


def person_by_shortname(shortname: str) -> Person | None:
    """Return the person with this ``onPremisesSamAccountName``, or ``None``."""
    shortname = (shortname or "").strip()
    if not shortname:
        return None
    if is_fake():
        from . import _fake

        wanted = shortname.casefold()
        return next(
            (p for p in _people_from(_fake.people()) if p.shortname.casefold() == wanted),
            None,
        )
    return _graph_one(f"onPremisesSamAccountName eq '{_quote(shortname)}'")


def person_by_id(object_id: str) -> Person | None:
    """Return the person with this Entra object id, or ``None``.

    Used to recover a shortname when the sign-in token does not carry the
    ``onPremisesSamAccountName`` claim.
    """
    object_id = (object_id or "").strip()
    if not object_id:
        return None
    if is_fake():
        from . import _fake

        return next(
            (p for p in _people_from(_fake.people()) if p.id == object_id),
            None,
        )
    from . import _graph
    from .errors import GraphError

    try:
        payload = _graph.get(f"/users/{object_id}", params={"$select": _SELECT})
    except GraphError as exc:
        if exc.status == 404:
            return None
        raise
    return _to_person(payload)


# --- backends --------------------------------------------------------------

def _is_employee(person: Person) -> bool:
    # Matches the LDAP filter this replaces, which was `(employeeType=*h*)`.
    return "h" in person.employee_type.casefold()


def _fake_people(tokens: list[str]) -> list[Person]:
    from . import _fake

    people = _people_from(_fake.people())
    return [p for p in people if all(_token_rank(t, _values(p)) != RANK_NO_MATCH for t in tokens)]


def _values(person: Person) -> tuple[str, ...]:
    return (person.display_name, person.email, person.shortname)


def _people_from(raw: list[dict[str, Any]]) -> list[Person]:
    return [
        Person(
            shortname=row.get("shortname", ""),
            display_name=row.get("display_name", ""),
            email=row.get("email", ""),
            id=row.get("id", ""),
            employee_type=row.get("employee_type", ""),
        )
        for row in raw
    ]


def _quote(value: str) -> str:
    """Escape a value for an OData string literal."""
    return value.replace("'", "''")


def _token_filter(token: str) -> str:
    quoted = _quote(token)
    return "(" + " or ".join(f"startsWith({f},'{quoted}')" for f in _SEARCH_FIELDS) + ")"


def _graph_people(tokens: list[str]) -> list[Person]:
    from . import _graph

    top = int(env("APPKIT_DIRECTORY_TOP", "200") or "200")
    clauses = [_token_filter(token) for token in tokens]
    extra = env("APPKIT_DIRECTORY_EMPLOYEE_FILTER", "")
    if extra:
        clauses.append(f"({extra})")

    rows = _graph.get_all(
        "/users",
        params={"$filter": " and ".join(clauses), "$select": _SELECT, "$top": top},
        # $filter on onPremisesSamAccountName is an advanced query; Graph
        # refuses it without this header.
        headers={"ConsistencyLevel": "eventual"},
    )
    return [_to_person(row) for row in rows]


def _graph_one(odata_filter: str) -> Person | None:
    from . import _graph

    rows = _graph.get_all(
        "/users",
        params={"$filter": odata_filter, "$select": _SELECT, "$top": 2},
        headers={"ConsistencyLevel": "eventual"},
    )
    return _to_person(rows[0]) if rows else None


def _to_person(row: dict[str, Any]) -> Person:
    return Person(
        shortname=(row.get("onPremisesSamAccountName") or "").strip(),
        display_name=(row.get("displayName") or "").strip(),
        email=(row.get("mail") or "").strip(),
        id=(row.get("id") or "").strip(),
        employee_type=(row.get("employeeType") or "").strip(),
    )
