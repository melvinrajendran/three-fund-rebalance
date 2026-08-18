from decimal import Decimal
from pathlib import Path

import pytest
import requests

from three_fund_rebalance import vt_allocation
from three_fund_rebalance.vt_allocation import (
    VTFetchError,
    _extract_us_pct_and_as_of,
    fetch_vt_us_pct,
)

FIXTURES_DIR = Path(__file__).parent / "fixtures"
REAL_FACT_SHEET_TEXT = (FIXTURES_DIR / "vt_fact_sheet_sample_text.txt").read_text()


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


class TestFetchVtUsPct:
    """Network/PDF-parsing orchestration, with requests and pypdf mocked out
    so these tests run offline and fast."""

    def test_network_failure_raises_vt_fetch_error(self, monkeypatch):
        def raise_connection_error(*args, **kwargs):
            raise requests.ConnectionError("boom")

        monkeypatch.setattr(vt_allocation.requests, "get", raise_connection_error)
        with pytest.raises(VTFetchError, match="Failed to download"):
            fetch_vt_us_pct()

    def test_http_error_status_raises_vt_fetch_error(self, monkeypatch):
        class FakeResponse:
            def raise_for_status(self):
                raise requests.HTTPError("404 Client Error")

        monkeypatch.setattr(
            vt_allocation.requests, "get", lambda *a, **k: FakeResponse()
        )
        with pytest.raises(VTFetchError, match="Failed to download"):
            fetch_vt_us_pct()

    def test_unparseable_pdf_raises_vt_fetch_error(self, monkeypatch):
        class FakeResponse:
            content = b"not actually a pdf"

            def raise_for_status(self):
                pass

        monkeypatch.setattr(
            vt_allocation.requests, "get", lambda *a, **k: FakeResponse()
        )
        with pytest.raises(VTFetchError, match="Failed to parse"):
            fetch_vt_us_pct()

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

        result = fetch_vt_us_pct()
        assert result.us_pct == Decimal("61.9")
        assert result.as_of == "June 30, 2026"
        assert result.source == "vanguard_fact_sheet"
