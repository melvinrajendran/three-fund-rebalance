import json
from decimal import Decimal
from pathlib import Path

import pytest
import requests

from three_fund_rebalance import vt_allocation
from three_fund_rebalance.vt_allocation import (
    VTFetchError,
    _extract_us_pct_and_as_of,
    _extract_us_pct_from_diversification,
    fetch_from_api,
    fetch_from_fact_sheet,
    fetch_vt_us_pct,
)

FIXTURES_DIR = Path(__file__).parent / "fixtures"
REAL_FACT_SHEET_TEXT = (FIXTURES_DIR / "vt_fact_sheet_sample_text.txt").read_text()
REAL_DIVERSIFICATION = json.loads((FIXTURES_DIR / "vt_diversification_sample.json").read_text())


def fake_get(payload=None, *, content=None, status_error=None, json_error=False):
    """Build a stand-in for requests.get returning a canned response."""

    class FakeResponse:
        def __init__(self):
            self.content = content

        def raise_for_status(self):
            if status_error:
                raise status_error

        def json(self):
            if json_error:
                raise ValueError("Expecting value: line 1 column 1 (char 0)")
            return payload

    return lambda *a, **k: FakeResponse()


class TestExtractUsPctAndAsOf:
    """Parsing logic tested directly against a real pypdf extraction of
    Vanguard's VT fact sheet, so these tests catch real formatting quirks
    (e.g. pypdf dropping spaces: "as %of common stock") without touching
    the network."""

    def test_parses_real_fact_sheet_text(self):
        us_pct, as_of = _extract_us_pct_and_as_of(REAL_FACT_SHEET_TEXT)
        assert us_pct == Decimal("61.9")
        assert as_of == "June 30, 2026"

    def test_ignores_unrelated_united_states_mentions_before_the_table(self):
        # The fact sheet mentions "the United States" in prose well before
        # the actual allocations table -- make sure we don't match that.
        text = (
            "including the United States prose mention. "
            "Ten largest market allocations as % of common stock "
            "United States 55.0 %Japan 10.0 %"
        )
        us_pct, _ = _extract_us_pct_and_as_of(text)
        assert us_pct == Decimal("55.0")

    def test_missing_table_raises(self):
        with pytest.raises(VTFetchError, match="Could not find"):
            _extract_us_pct_and_as_of("This document has no allocation table at all.")

    def test_out_of_range_percentage_raises(self):
        text = "market allocations as % of common stock United States 150 %Japan 5 %"
        with pytest.raises(VTFetchError, match="out of range"):
            _extract_us_pct_and_as_of(text)

    def test_unparseable_number_does_not_match(self):
        # "abc" isn't matched by \d, so this should fall through to "not found"
        # rather than raising a confusing number-parsing error.
        text = "market allocations as % of common stock United States abc %"
        with pytest.raises(VTFetchError, match="Could not find"):
            _extract_us_pct_and_as_of(text)

    def test_malformed_number_that_matches_the_pattern_raises_parse_error(self):
        # "6.1.9" matches [\d.]+ (the character class allows multiple dots)
        # but isn't a valid Decimal -- this exercises the distinct "matched
        # but couldn't parse" error path, as opposed to "no match at all".
        text = "market allocations as % of common stock United States 6.1.9 %"
        with pytest.raises(VTFetchError, match="could not parse it as a number"):
            _extract_us_pct_and_as_of(text)

    def test_missing_as_of_falls_back_to_unknown(self):
        text = "market allocations as % of common stock United States 61.9 %"
        _, as_of = _extract_us_pct_and_as_of(text)
        assert as_of == "unknown date"

    def test_result_ex_us_pct_is_complement(self):
        us_pct, as_of = _extract_us_pct_and_as_of(REAL_FACT_SHEET_TEXT)
        result = vt_allocation.VTAllocationResult(
            us_pct=us_pct, as_of=as_of, source="vanguard_fact_sheet"
        )
        assert result.ex_us_pct == Decimal("38.1")


class TestExtractFromDiversification:
    """Parsing of the monthly country-diversification API payload, tested
    against a real saved response from Vanguard."""

    def test_parses_real_payload(self):
        us_pct, as_of = _extract_us_pct_from_diversification(REAL_DIVERSIFICATION)
        assert us_pct == Decimal("62.0")
        assert as_of == "July 31, 2026"

    def test_reads_current_period_not_prior_year(self):
        # The payload carries both; picking the wrong key would silently use
        # data a full year stale (63.5% as of 2025-07-31 in this fixture).
        us_pct, _ = _extract_us_pct_from_diversification(REAL_DIVERSIFICATION)
        prior = next(
            i["prevYrPct"]
            for i in REAL_DIVERSIFICATION["country"]["item"]
            if i["name"].strip() == "United States"
        )
        assert us_pct != Decimal(prior)

    def test_missing_country_block_raises(self):
        with pytest.raises(VTFetchError, match="did not contain a country breakdown"):
            _extract_us_pct_from_diversification({"sector": {}})

    def test_missing_united_states_entry_raises(self):
        payload = {"country": {"currentAsOfDate": "", "item": [{"name": "Japan", "currYrPct": "5.8"}]}}
        with pytest.raises(VTFetchError, match="no 'United States' entry"):
            _extract_us_pct_from_diversification(payload)

    def test_null_current_percentage_raises(self):
        payload = {
            "country": {"currentAsOfDate": "", "item": [{"name": "United States", "currYrPct": None}]}
        }
        with pytest.raises(VTFetchError, match=r"no current-period U\.S\. percentage"):
            _extract_us_pct_from_diversification(payload)

    def test_unparseable_percentage_raises(self):
        payload = {
            "country": {"currentAsOfDate": "", "item": [{"name": "United States", "currYrPct": "abc"}]}
        }
        with pytest.raises(VTFetchError, match="Could not parse"):
            _extract_us_pct_from_diversification(payload)

    def test_out_of_range_percentage_raises(self):
        payload = {
            "country": {"currentAsOfDate": "", "item": [{"name": "United States", "currYrPct": "150"}]}
        }
        with pytest.raises(VTFetchError, match="out of range"):
            _extract_us_pct_from_diversification(payload)

    def test_malformed_as_of_falls_back_to_raw_string(self):
        payload = {
            "country": {
                "currentAsOfDate": "not-a-date",
                "item": [{"name": "United States", "currYrPct": "62.0"}],
            }
        }
        _, as_of = _extract_us_pct_from_diversification(payload)
        assert as_of == "not-a-date"


class TestFetchFromApi:
    def test_successful_fetch(self, monkeypatch):
        monkeypatch.setattr(vt_allocation.requests, "get", fake_get(REAL_DIVERSIFICATION))
        result = fetch_from_api()
        assert result.us_pct == Decimal("62.0")
        assert result.as_of == "July 31, 2026"
        assert result.source == "vanguard_api"

    def test_network_failure_raises(self, monkeypatch):
        def boom(*a, **k):
            raise requests.ConnectionError("boom")

        monkeypatch.setattr(vt_allocation.requests, "get", boom)
        with pytest.raises(VTFetchError, match="Failed to download VT diversification"):
            fetch_from_api()

    def test_html_bot_block_response_raises(self, monkeypatch):
        # Bot protection returns the HTML app shell, which fails JSON decoding.
        monkeypatch.setattr(vt_allocation.requests, "get", fake_get(json_error=True))
        with pytest.raises(VTFetchError, match="not valid JSON"):
            fetch_from_api()


class TestFetchChain:
    """fetch_vt_us_pct tries the monthly API first and falls back to the
    quarterly PDF, so a break in either source alone is survivable."""

    def test_prefers_api_when_it_works(self, monkeypatch):
        monkeypatch.setattr(
            vt_allocation,
            "fetch_from_api",
            lambda timeout=None: vt_allocation.VTAllocationResult(
                us_pct=Decimal("62.0"), as_of="July 31, 2026", source="vanguard_api"
            ),
        )
        monkeypatch.setattr(
            vt_allocation,
            "fetch_from_fact_sheet",
            lambda timeout=None: pytest.fail("should not fall back when the API works"),
        )
        assert fetch_vt_us_pct().source == "vanguard_api"

    def test_falls_back_to_fact_sheet_when_api_fails(self, monkeypatch):
        def api_boom(timeout=None):
            raise VTFetchError("api down")

        monkeypatch.setattr(vt_allocation, "fetch_from_api", api_boom)
        monkeypatch.setattr(
            vt_allocation,
            "fetch_from_fact_sheet",
            lambda timeout=None: vt_allocation.VTAllocationResult(
                us_pct=Decimal("61.9"), as_of="June 30, 2026", source="vanguard_fact_sheet"
            ),
        )
        result = fetch_vt_us_pct()
        assert result.source == "vanguard_fact_sheet"
        assert result.us_pct == Decimal("61.9")

    def test_reports_every_failure_when_all_sources_fail(self, monkeypatch):
        def api_boom(timeout=None):
            raise VTFetchError("api down")

        def pdf_boom(timeout=None):
            raise VTFetchError("pdf down")

        monkeypatch.setattr(vt_allocation, "fetch_from_api", api_boom)
        monkeypatch.setattr(vt_allocation, "fetch_from_fact_sheet", pdf_boom)
        with pytest.raises(VTFetchError, match="api down; pdf down"):
            fetch_vt_us_pct()


class TestFetchFromFactSheet:
    """Network/PDF-parsing orchestration, with requests and pypdf mocked out
    so these tests run offline and fast."""

    def test_network_failure_raises_vt_fetch_error(self, monkeypatch):
        def raise_connection_error(*args, **kwargs):
            raise requests.ConnectionError("boom")

        monkeypatch.setattr(vt_allocation.requests, "get", raise_connection_error)
        with pytest.raises(VTFetchError, match="Failed to download"):
            fetch_from_fact_sheet()

    def test_http_error_status_raises_vt_fetch_error(self, monkeypatch):
        class FakeResponse:
            def raise_for_status(self):
                raise requests.HTTPError("404 Client Error")

        monkeypatch.setattr(
            vt_allocation.requests, "get", lambda *a, **k: FakeResponse()
        )
        with pytest.raises(VTFetchError, match="Failed to download"):
            fetch_from_fact_sheet()

    def test_unparseable_pdf_raises_vt_fetch_error(self, monkeypatch):
        class FakeResponse:
            content = b"not actually a pdf"

            def raise_for_status(self):
                pass

        monkeypatch.setattr(
            vt_allocation.requests, "get", lambda *a, **k: FakeResponse()
        )
        with pytest.raises(VTFetchError, match="Failed to parse"):
            fetch_from_fact_sheet()

    def test_successful_fetch_returns_result(self, monkeypatch):
        class FakePage:
            def extract_text(self):
                return REAL_FACT_SHEET_TEXT

        class FakeReader:
            def __init__(self, _bytes_io):
                self.pages = [FakePage()]

        class FakeResponse:
            content = b"fake pdf bytes"

            def raise_for_status(self):
                pass

        monkeypatch.setattr(
            vt_allocation.requests, "get", lambda *a, **k: FakeResponse()
        )
        monkeypatch.setattr(vt_allocation, "PdfReader", FakeReader)

        result = fetch_from_fact_sheet()
        assert result.us_pct == Decimal("61.9")
        assert result.as_of == "June 30, 2026"
        assert result.source == "vanguard_fact_sheet"
