"""The one defect that only time can prove fixed.

Azure hands out Entra access tokens that expire after about an hour. The
connection pool used to capture one when it was built, so an app worked all
morning and then quietly lost the ability to open or replace a connection. No
unit test, no CI job and no short live run can see that: every one of them
finishes inside the token's lifetime.

So this test outlives the token deliberately. It is marked ``soak`` and excluded
from every default selection; run it on its own, ideally as a scheduled
Container Apps Job::

    APPKIT_SOAK_MINUTES=75 pytest -m soak -s

``-s`` keeps the progress lines visible in the container log, so a run that is
still going does not look like a hung job.
"""

import os
import time

import pytest

from appkit import db
from appkit.doctor import _token_claims

pytestmark = pytest.mark.soak

DEFAULT_MINUTES = 75
PROBE_INTERVAL_SECONDS = 300


def _say(message: str) -> None:
    print(f"[soak] {message}", flush=True)


@pytest.fixture
def soak_minutes():
    return float(os.getenv("APPKIT_SOAK_MINUTES", DEFAULT_MINUTES))


def test_the_pool_outlives_the_token_it_started_with(database, soak_minutes, monkeypatch):
    real = db._pg_password
    tokens = []

    def recording():
        value = real()
        tokens.append(value)
        return value

    monkeypatch.setattr(db, "_pg_password", recording)
    db._reset_pool()

    pool = db._pool()
    pool.wait(timeout=30)
    assert tokens, "the pool opened no connection"

    first_expiry = _token_claims(tokens[0]).get("exp")
    if not first_expiry:
        pytest.skip("could not read `exp` from the first token; nothing to outlive")

    _say(
        f"first token expires in {(first_expiry - time.time()) / 60:.0f} min; "
        f"soaking for {soak_minutes:.0f} min"
    )

    deadline = time.time() + soak_minutes * 60
    probes = 0
    while time.time() < deadline:
        time.sleep(min(PROBE_INTERVAL_SECONDS, max(1.0, deadline - time.time())))
        assert db.query("select 1 as ok")[0]["ok"] == 1
        probes += 1
        _say(f"probe {probes} ok, {(deadline - time.time()) / 60:.0f} min remaining")

    if time.time() <= first_expiry:
        pytest.skip(
            f"the soak ended before the first token expired — raise "
            f"APPKIT_SOAK_MINUTES above {(first_expiry - time.time()) / 60 + soak_minutes:.0f}"
        )

    _say("the original token has now expired; forcing a new connection")

    # The regression: opening a *new* physical connection after the token the
    # pool was built with has expired. Holding two at once makes the pool grow.
    with pool.connection(), pool.connection():
        pass

    assert len(tokens) >= 2, "the pool did not open a new connection to test"
    assert tokens[-1] != tokens[0], (
        "the pool reused the token it was built with, which has now expired"
    )
    assert db.query("select 1 as ok")[0]["ok"] == 1

    _say(f"survived {probes} probes and a token rotation over {soak_minutes:.0f} min")
    db._reset_pool()
