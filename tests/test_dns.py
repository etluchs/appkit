"""DNS lookups on the fake backend, plus the error mapping the real one uses.

Note the import style: dnspython's top-level package is also called ``dns``, so
the module under test is imported as ``appkit_dns`` to keep the two apart. Only
appkit ever imports dnspython, so the collision does not reach app code.
"""

import pytest

import appkit
from appkit import dns as appkit_dns
from appkit.dns import DnsLookupError


def test_a_seeded_name_exists():
    assert appkit_dns.cname_exists("taken.azr.uzh.ch") is True


def test_an_unknown_name_does_not_exist():
    assert appkit_dns.cname_exists("free.azr.uzh.ch") is False


def test_names_are_normalized_before_lookup():
    assert appkit_dns.cname_exists("  TAKEN.AZR.UZH.CH.  ") is True


def test_an_empty_name_is_rejected():
    with pytest.raises(ValueError):
        appkit_dns.cname_exists("   ")


def test_the_fake_name_set_can_be_replaced():
    appkit._fake.set_dns_names({"Mine.Azr.UZH.ch"})
    assert appkit_dns.cname_exists("mine.azr.uzh.ch") is True
    assert appkit_dns.cname_exists("taken.azr.uzh.ch") is False


def test_reset_fakes_restores_the_seed():
    appkit._fake.set_dns_names(set())
    appkit.reset_fakes()
    assert appkit_dns.cname_exists("taken.azr.uzh.ch") is True


def test_resolve_returns_the_records():
    assert appkit_dns.resolve("taken.azr.uzh.ch") == ["taken.azr.uzh.ch"]
    assert appkit_dns.resolve("free.azr.uzh.ch") == []


def test_a_missing_name_is_absence_not_failure(monkeypatch):
    """NXDOMAIN means "free", which is the whole point of the check."""
    import dns.resolver as resolver_module

    monkeypatch.setenv("APPKIT_BACKEND", "azure")

    def explode(*args, **kwargs):
        raise resolver_module.NXDOMAIN()

    monkeypatch.setattr(resolver_module.Resolver, "resolve", explode)
    assert appkit_dns.cname_exists("free.azr.uzh.ch") is False


def test_an_unreachable_resolver_raises_rather_than_reporting_free(monkeypatch):
    """A timeout must never be reported as "the name is available"."""
    import dns.exception as dns_exception
    import dns.resolver as resolver_module

    monkeypatch.setenv("APPKIT_BACKEND", "azure")

    def explode(*args, **kwargs):
        raise dns_exception.Timeout()

    monkeypatch.setattr(resolver_module.Resolver, "resolve", explode)
    with pytest.raises(DnsLookupError):
        appkit_dns.cname_exists("anything.azr.uzh.ch")
