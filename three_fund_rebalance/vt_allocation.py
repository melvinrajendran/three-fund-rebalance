"""Fetches VT's (Vanguard Total World Stock ETF) US vs. ex-US stock weighting.

This is used to translate a user's stock/bond target into a domestic/
international equity split, on the theory that VT's market-cap weighting
*is* "the world stock market's" domestic/international split.

Vanguard's interactive fund-profile page (investor.vanguard.com) is behind
Akamai bot protection and is client-side rendered, which makes it a poor
scrape target. Instead we pull Vanguard's own quarterly fact sheet PDF,
which is a static file hosted on a separate, unprotected docs subdomain:

    https://fund-docs.vanguard.com/F3141.pdf   (3141 = VT's Vanguard fund ID)

It contains a "Ten largest market allocations as % of common stock" table
whose first line is always "United States <pct>%" -- ex-US is the remainder.
"""

from __future__ import annotations

import io
import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

import requests
from pypdf import PdfReader

from three_fund_rebalance.config import VT_FACT_SHEET_URL

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
    source: str  # "vanguard_fact_sheet", "cache", or "manual"

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


def fetch_vt_us_pct(
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
