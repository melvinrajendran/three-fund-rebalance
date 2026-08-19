"""Fetches VT's (Vanguard Total World Stock ETF) US vs. ex-US stock weighting.

This is used to translate a user's stock/bond target into a domestic/
international equity split, on the theory that VT's market-cap weighting
*is* "the world stock market's" domestic/international split.

Two independent Vanguard sources are tried, in freshness order:

1. The JSON endpoint behind the fund profile page's country diversification
   table. Refreshed monthly, so it leads the fact sheet by up to a quarter.
   Its `country.item[]` list carries a "United States" entry with the
   current-period percentage.

2. Vanguard's quarterly fact sheet PDF (fund ID 3141), a static file on a
   docs subdomain outside the interactive site's bot protection, containing
   a "Ten largest market allocations as % of common stock" table.

The page's own HTML is deliberately not scraped: it is client-side rendered
behind Akamai bot protection, so it would need a headless browser and would
still break often. Either source above is a plain HTTP GET.

In both cases ex-US is taken as the remainder (100 - US).
"""

from __future__ import annotations

import io
import re
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation

import requests
from pypdf import PdfReader

from three_fund_rebalance.config import VT_DIVERSIFICATION_API_URL, VT_FACT_SHEET_URL

DEFAULT_TIMEOUT_SECONDS = 10.0

# Non-greedy, bounded lookahead: anchor on the table heading, then take the
# first "United States <num>%" that follows within a reasonable distance.
# Bounding the distance (rather than searching the whole document) avoids
# accidentally matching an unrelated "United States" mention elsewhere in
# the fact sheet's boilerplate/legal text if the heading text ever changes.
_US_PCT_PATTERN = re.compile(
    r"market allocations.{0,300}?United States\s+([\d.]+)\s*%",
    re.IGNORECASE | re.DOTALL,
)
_AS_OF_PATTERN = re.compile(r"[Aa]s of\s+([A-Z][a-z]+ \d{1,2},\s*\d{4})")


class VTFetchError(Exception):
    """Raised when the live VT allocation can't be fetched or parsed. Callers
    are expected to catch this and fall back to a cached or manually entered
    value -- it is never allowed to crash the whole CLI run."""


@dataclass(frozen=True)
class VTAllocationResult:
    us_pct: Decimal
    as_of: str
    # "vanguard_api", "vanguard_fact_sheet", "cache", or "manual"
    source: str

    @property
    def ex_us_pct(self) -> Decimal:
        return Decimal(100) - self.us_pct


def _extract_us_pct_and_as_of(text: str) -> tuple[Decimal, str]:
    """Pure text-parsing step, kept separate from the network/PDF I/O so it
    can be unit tested against a saved fact sheet extraction without hitting
    the network or a PDF library."""
    match = _US_PCT_PATTERN.search(text)
    if not match:
        raise VTFetchError(
            "Could not find a 'United States' market-allocation percentage "
            "in the VT fact sheet text -- the document layout may have changed."
        )
    try:
        us_pct = Decimal(match.group(1))
    except InvalidOperation as exc:
        raise VTFetchError(
            f"Found a US allocation match but could not parse it as a number: {match.group(1)!r}"
        ) from exc
    if not (Decimal(0) < us_pct <= Decimal(100)):
        raise VTFetchError(f"Parsed US allocation percentage is out of range: {us_pct}")

    as_of_match = _AS_OF_PATTERN.search(text)
    as_of = as_of_match.group(1) if as_of_match else "unknown date"
    return us_pct, as_of


def _format_as_of(raw: str) -> str:
    """Render the API's ISO timestamp the same way the fact sheet spells its
    date ("July 31, 2026"), so the two sources read identically to the user.
    Falls back to the raw string if the format ever changes."""
    try:
        return datetime.fromisoformat(raw).strftime("%B %-d, %Y")
    except (ValueError, TypeError):
        return raw or "unknown date"


def _extract_us_pct_from_diversification(payload: dict) -> tuple[Decimal, str]:
    """Pure parsing step for the diversification API payload, split out from
    the network call so it can be unit tested against a saved response."""
    try:
        country = payload["country"]
        items = country["item"]
    except (KeyError, TypeError) as exc:
        raise VTFetchError(
            "VT diversification response did not contain a country breakdown "
            "-- the API shape may have changed."
        ) from exc

    for item in items:
        if not isinstance(item, dict) or item.get("name", "").strip() != "United States":
            continue
        raw_pct = item.get("currYrPct")
        if raw_pct in (None, ""):
            raise VTFetchError("VT diversification response has no current-period US percentage.")
        try:
            us_pct = Decimal(str(raw_pct))
        except InvalidOperation as exc:
            raise VTFetchError(f"Could not parse US allocation percentage: {raw_pct!r}") from exc
        if not (Decimal(0) < us_pct <= Decimal(100)):
            raise VTFetchError(f"Parsed US allocation percentage is out of range: {us_pct}")
        return us_pct, _format_as_of(country.get("currentAsOfDate", ""))

    raise VTFetchError("VT diversification response had no 'United States' entry.")


def fetch_from_api(
    url: str = VT_DIVERSIFICATION_API_URL, timeout: float = DEFAULT_TIMEOUT_SECONDS
) -> VTAllocationResult:
    """Fetch the monthly country diversification JSON. Raises VTFetchError on
    any network, HTTP, JSON, or shape failure -- never guesses."""
    try:
        response = requests.get(
            url,
            timeout=timeout,
            headers={"User-Agent": "three-fund-rebalance/0.1", "Accept": "application/json"},
        )
        response.raise_for_status()
        payload = response.json()
    except requests.RequestException as exc:
        raise VTFetchError(f"Failed to download VT diversification data from {url}: {exc}") from exc
    except ValueError as exc:
        # Bot-protection responses come back as the HTML app shell, which
        # fails to decode as JSON -- surface that as a normal fetch failure.
        raise VTFetchError(f"VT diversification response was not valid JSON: {exc}") from exc

    us_pct, as_of = _extract_us_pct_from_diversification(payload)
    return VTAllocationResult(us_pct=us_pct, as_of=as_of, source="vanguard_api")


def fetch_from_fact_sheet(
    url: str = VT_FACT_SHEET_URL, timeout: float = DEFAULT_TIMEOUT_SECONDS
) -> VTAllocationResult:
    """Download and parse Vanguard's VT fact sheet PDF. Raises VTFetchError
    on any network, HTTP, PDF-parsing, or format-mismatch failure -- never
    returns a partial/guessed result."""
    try:
        response = requests.get(
            url, timeout=timeout, headers={"User-Agent": "three-fund-rebalance/0.1"}
        )
        response.raise_for_status()
    except requests.RequestException as exc:
        raise VTFetchError(f"Failed to download VT fact sheet from {url}: {exc}") from exc

    try:
        reader = PdfReader(io.BytesIO(response.content))
        text = "\n".join(page.extract_text() or "" for page in reader.pages)
    except Exception as exc:  # pypdf raises assorted exception types on bad PDFs
        raise VTFetchError(f"Failed to parse VT fact sheet PDF: {exc}") from exc

    us_pct, as_of = _extract_us_pct_and_as_of(text)
    return VTAllocationResult(us_pct=us_pct, as_of=as_of, source="vanguard_fact_sheet")


def fetch_vt_us_pct(timeout: float = DEFAULT_TIMEOUT_SECONDS) -> VTAllocationResult:
    """Try each live source in freshness order, returning the first that
    works. Raises VTFetchError listing every failure only if all of them do.

    The two sources sit on different hosts and use different formats, so a
    change or outage in one generally leaves the other intact.
    """
    failures = []
    for fetch in (fetch_from_api, fetch_from_fact_sheet):
        try:
            return fetch(timeout=timeout)
        except VTFetchError as exc:
            failures.append(str(exc))
    raise VTFetchError("; ".join(failures))
