"""Live checks that the VT sources still exist and still parse.

Every other test in this suite mocks the network, which is what lets it run
offline and in CI. The cost of that is a blind spot: a source can rot away
entirely without a single test noticing. One did. The JSON endpoint's URL
pointed under the fund profile page's SPA prefix, where the router answers
*any* path with 200 and the HTML app shell, so the primary source failed on
every run for every user while the suite stayed green and the README went on
describing two independent sources.

These tests are that blind spot's only cover. They are deselected by default
(see the `network` mark in pyproject.toml) because a source being down is not
a reason for someone's `pytest` run to fail. Run them deliberately, before a
release or whenever the fetch chain misbehaves:

    pytest -m network

A failure here is not necessarily a bug in this repo -- Vanguard may simply be
down. Check the URL by hand before changing code, and note that a 200 proves
nothing on its own: the response *body* is what distinguishes an endpoint from
the router's catch-all.
"""

import json
from decimal import Decimal
from pathlib import Path

import pytest
import requests

from three_fund_rebalance.config import FALLBACK_VT_US_PCT, VT_DIVERSIFICATION_API_URL
from three_fund_rebalance.vt_allocation import (
    fetch_from_api,
    fetch_from_fact_sheet,
    fetch_vt_us_pct,
)

pytestmark = pytest.mark.network

FIXTURES_DIR = Path(__file__).parent / "fixtures"
SAVED_DIVERSIFICATION = json.loads((FIXTURES_DIR / "vt_diversification_sample.json").read_text())

# Generous on purpose. These bounds are not a view on markets -- they exist to
# tell a plausible number from a parse that has gone wrong (a stray sector
# weight, a share count, a percentage read as a fraction).
PLAUSIBLE_MIN = Decimal(40)
PLAUSIBLE_MAX = Decimal(80)

# The two sources are up to a quarter apart, so they will not agree exactly.
# They should not be points apart either: that would mean one of them is being
# read wrong, which is precisely what a prior-year column looks like.
MAX_SOURCE_DISAGREEMENT = Decimal(3)

TIMEOUT = 30.0


def _shape(value):
    """The structural skeleton of a payload -- keys and container types, with
    the values themselves discarded. Comparing this against the saved fixture
    is what catches the API changing shape underneath the offline tests, which
    would otherwise keep passing against a fixture that no longer resembles
    anything Vanguard sends."""
    if isinstance(value, dict):
        return {key: _shape(inner) for key, inner in sorted(value.items())}
    if isinstance(value, list):
        return [_shape(value[0])] if value else []
    return type(value).__name__


class TestDiversificationEndpoint:
    def test_the_endpoint_still_returns_parseable_json(self):
        result = fetch_from_api(timeout=TIMEOUT)
        assert result.source == "vanguard_api"
        assert PLAUSIBLE_MIN < result.us_pct < PLAUSIBLE_MAX

    def test_the_url_is_not_the_spa_router_catch_all(self):
        # The specific way this broke before, called out by name so a failure
        # says what happened rather than "expected JSON, got str". Any path
        # under the profile page's prefix returns the app shell with a 200.
        assert "/investment-products/etfs/profile/" not in VT_DIVERSIFICATION_API_URL

    def test_the_saved_fixture_still_matches_the_live_payload(self):
        # Every offline test of the parser is written against that fixture, so
        # the fixture drifting out of date silently weakens all of them.
        live = requests.get(
            VT_DIVERSIFICATION_API_URL,
            timeout=TIMEOUT,
            headers={"Accept": "application/json"},
        ).json()
        assert _shape(live["country"]) == _shape(SAVED_DIVERSIFICATION["country"])


class TestFactSheet:
    def test_the_pdf_still_downloads_and_parses(self):
        result = fetch_from_fact_sheet(timeout=TIMEOUT)
        assert result.source == "vanguard_fact_sheet"
        assert PLAUSIBLE_MIN < result.us_pct < PLAUSIBLE_MAX


class TestTheChainAsAWhole:
    def test_the_chain_reaches_the_fresher_source_first(self):
        # Not merely that the chain returns something -- it always will, since
        # the fact sheet is behind it. The point is that the *primary* source
        # is the one answering, which is the claim that was false before.
        assert fetch_vt_us_pct(timeout=TIMEOUT).source == "vanguard_api"

    def test_the_two_sources_agree(self):
        # Independent formats on separate hosts landing points apart means one
        # of them is being read wrong, whatever each parser thinks.
        api = fetch_from_api(timeout=TIMEOUT).us_pct
        fact_sheet = fetch_from_fact_sheet(timeout=TIMEOUT).us_pct
        assert abs(api - fact_sheet) <= MAX_SOURCE_DISAGREEMENT

    def test_the_baked_in_fallback_has_not_drifted_far_from_reality(self):
        # FALLBACK_VT_US_PCT is offered as a suggested default to someone with
        # no network and no cache. It is allowed to be stale, but a fallback
        # several points out is worse than no suggestion at all -- bump it.
        live = fetch_vt_us_pct(timeout=TIMEOUT).us_pct
        assert abs(live - FALLBACK_VT_US_PCT) <= Decimal(5)
