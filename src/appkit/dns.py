"""Ask the DNS whether a name is already taken.

Public surface::

    from appkit import dns

    if dns.cname_exists("myapp.azr.uzh.ch"):
        ...

Used to tell someone that the hostname they just typed is already in use,
before they submit a form asking for it.

Unlike the other modules, the ``azure`` backend here is not an Azure service —
it is simply a real resolver, against ``APPKIT_DNS_SERVER`` when set. The
``fake`` backend answers from an in-memory set of names, so tests need no
network. The switch is the same ``APPKIT_BACKEND`` either way.
"""

from __future__ import annotations

from .config import env, is_fake
from .errors import AppkitError

DEFAULT_TIMEOUT = 2.0
DEFAULT_LIFETIME = 4.0


class DnsLookupError(AppkitError):
    """The lookup could not be completed — a timeout, or no usable nameserver.

    Distinct from "the name does not exist", which is a plain ``False``: a
    caller checking availability must not report a name as free just because
    the resolver was unreachable.
    """


def normalize(name: str) -> str:
    """Lower-case, strip whitespace and drop a trailing root dot."""
    normalized = (name or "").strip().lower().rstrip(".")
    if not normalized:
        raise ValueError("A DNS name is required.")
    return normalized


def cname_exists(name: str) -> bool:
    """Return whether ``name`` resolves to a CNAME record.

    Raises:
        ValueError: If ``name`` is empty.
        DnsLookupError: If the lookup could not be completed.
    """
    return bool(resolve(name, "CNAME"))


def resolve(name: str, rtype: str = "CNAME") -> list[str]:
    """Return the records of type ``rtype`` for ``name`` (empty if none exist).

    Raises:
        ValueError: If ``name`` is empty.
        DnsLookupError: If the lookup could not be completed.
    """
    normalized = normalize(name)
    if is_fake():
        from . import _fake

        return [normalized] if normalized in _fake.dns_names() else []
    return _resolve_real(normalized, rtype)


def _resolve_real(name: str, rtype: str) -> list[str]:
    import dns.exception
    import dns.resolver

    resolver = dns.resolver.Resolver(configure=True)
    server = env("APPKIT_DNS_SERVER", "")
    if server:
        resolver.nameservers = [server]
    resolver.timeout = float(env("APPKIT_DNS_TIMEOUT", str(DEFAULT_TIMEOUT)) or DEFAULT_TIMEOUT)
    resolver.lifetime = float(env("APPKIT_DNS_LIFETIME", str(DEFAULT_LIFETIME)) or DEFAULT_LIFETIME)

    try:
        answer = resolver.resolve(name, rtype)
    except (dns.resolver.NXDOMAIN, dns.resolver.NoAnswer):
        return []
    except (dns.resolver.NoNameservers, dns.exception.Timeout) as exc:
        raise DnsLookupError(f"Could not look up {name!r}: {exc}") from exc
    return [str(record).rstrip(".") for record in answer]
