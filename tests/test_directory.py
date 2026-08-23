"""Directory behaviour on the fake backend.

The relevance assertions are ported from the LDAP directory this module
replaces — they are the reason the ranking code exists, and they are what a
user notices if it regresses.
"""

import pytest

import appkit
from appkit import directory
from appkit.directory import MAX_SEARCH_TOKENS, Person, SearchTermTooManyTermsError


def names(people):
    return [p.display_name for p in people]


def test_finds_a_person_by_a_prefix_of_their_name():
    assert "Amelia Stucki" in names(directory.search_people("ameli"))


def test_token_order_does_not_matter():
    surname_first = directory.search_people("Hartmann, Nicole")
    given_first = directory.search_people("Nicole Hartmann")
    assert names(surname_first) == names(given_first) == ["Nicole Hartmann"]


def test_every_token_must_match():
    assert directory.search_people("nicole stucki") == []


def test_a_leading_exact_match_outranks_an_inner_one():
    # "tina" is Tina Aeberli's given name, Giulia's middle name, and a
    # substring of the login "tinadl".
    assert names(directory.search_people("tina"))[0] == "Tina Aeberli"


def test_a_prefix_match_still_appears_but_ranks_below_an_exact_one():
    result = names(directory.search_people("sandra"))
    assert result.index("Sandra Meier") < result.index("Alessandra Rossi")


def test_an_empty_term_is_rejected():
    with pytest.raises(ValueError):
        directory.search_people("   ")


def test_too_many_terms_are_rejected():
    with pytest.raises(SearchTermTooManyTermsError):
        directory.search_people(" ".join(["x"] * (MAX_SEARCH_TOKENS + 1)))


def test_exclude_accepts_a_shortname():
    assert directory.search_people("rossi", exclude=["crossi"]) == [
        p for p in directory.search_people("rossi") if p.shortname != "crossi"
    ]


def test_exclude_accepts_an_email_case_insensitively():
    assert "Carla Rossi" not in names(
        directory.search_people("rossi", exclude=["Carla.Rossi@UZH.CH"])
    )


def test_employees_only_drops_students():
    assert "Lara Studer" in names(directory.search_people("studer"))
    assert "Lara Studer" not in names(directory.search_people("studer", employees_only=True))


def test_limit_caps_the_result():
    assert len(directory.search_people("u", limit=3)) <= 3


def test_two_people_can_share_a_mailbox_but_not_a_shortname():
    """The case that makes email unusable as an identity key."""
    people = directory.search_people("it-support@uzh.ch")
    assert {p.shortname for p in people} == {"nhartma", "pfrei"}
    assert {p.email for p in people} == {"it-support@uzh.ch"}


def test_person_by_shortname_round_trips():
    assert directory.person_by_shortname("nhartma").display_name == "Nicole Hartmann"
    assert directory.person_by_shortname("nobody") is None
    assert directory.person_by_shortname("") is None


def test_person_by_id_round_trips():
    assert directory.person_by_id("u-pfrei").shortname == "pfrei"
    assert directory.person_by_id("u-nope") is None


def test_the_fake_directory_can_be_replaced():
    appkit._fake.set_people(
        [{"shortname": "zztop", "display_name": "Zoe Zimmermann", "email": "zoe@uzh.ch"}]
    )
    assert directory.search_people("zoe") == [
        Person(shortname="zztop", display_name="Zoe Zimmermann", email="zoe@uzh.ch")
    ]


def test_reset_fakes_restores_the_seed():
    appkit._fake.set_people([])
    appkit.reset_fakes()
    assert directory.search_people("amelia")


def test_somebody_with_no_shortname_is_not_offered():
    """Nothing can be authorized against them, so picking them only moves the
    failure downstream."""
    appkit._fake.set_people(
        [{"shortname": "", "display_name": "Ghost Account", "email": "ghost@uzh.ch"}]
    )
    assert directory.search_people("ghost") == []


def test_somebody_with_no_mailbox_is_not_offered():
    """They would be added to an approval chain that then cannot notify them."""
    appkit._fake.set_people(
        [{"shortname": "noinbox", "display_name": "No Inbox", "email": ""}]
    )
    assert directory.search_people("inbox") == []


def test_a_login_repeated_in_the_display_name_is_trimmed():
    appkit._fake.set_people(
        [{"shortname": "nhartma", "display_name": "Nicole Hartmann (nhartma)",
          "email": "n@uzh.ch"}]
    )
    assert directory.search_people("nicole")[0].display_name == "Nicole Hartmann"


def test_a_parenthetical_that_is_not_the_login_is_left_alone():
    appkit._fake.set_people(
        [{"shortname": "amuster", "display_name": "Anna Muster (ZI)", "email": "a@uzh.ch"}]
    )
    assert directory.search_people("anna")[0].display_name == "Anna Muster (ZI)"
