"""Constants and defaults shared across the package."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from three_fund_rebalance.models import TaxTreatment

# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------

SCHEMA_VERSION = 1
DEFAULT_CONFIG_PATH = Path.home() / ".three_fund_rebalance" / "config.json"

# ---------------------------------------------------------------------------
# Trading
# ---------------------------------------------------------------------------

# Fidelity's fractional-share ("Stocks by the Slice") minimum is $1, and it
# applies the same way in a Roth IRA as in a taxable brokerage account, so
# the lesser of the two is $1. Trades smaller than this are not actionable
# and are dropped from the recommendation rather than shown as noise.
# https://www.fidelity.com/trading/fractional-shares
MIN_TRADE_DOLLARS = Decimal("1.00")

# ---------------------------------------------------------------------------
# VT (Vanguard Total World Stock ETF) US / ex-US weighting
# ---------------------------------------------------------------------------

# Primary source: the JSON endpoint backing the fund profile page's country
# diversification table. It is refreshed monthly, so it leads the quarterly
# fact sheet by up to three months (e.g. 62.0% as of 2026-07-31 versus 61.9%
# as of 2026-06-30).
VT_DIVERSIFICATION_API_URL = (
    "https://investor.vanguard.com/investment-products/etfs/profile/api/vt/diversification"
)

# Fallback source: Vanguard's quarterly fact sheet PDF for VT (fund ID 3141),
# a static file on a docs subdomain that sits outside the interactive site's
# bot-protection layer. It publishes a "Ten largest market allocations as %
# of common stock" table with a "United States" line. Staler than the API,
# but served by entirely separate infrastructure, so it is unlikely to fail
# at the same time.
VT_FACT_SHEET_URL = "https://fund-docs.vanguard.com/F3141.pdf"

# Baked-in emergency fallback, only ever offered as a *suggested default* in
# the manual-entry prompt (never used silently) when both the live fetch and
# any previously cached value are unavailable, e.g. on a brand new machine
# with no network access.
FALLBACK_VT_US_PCT = Decimal("62.0")
FALLBACK_VT_AS_OF = "2026-07-31"

# ---------------------------------------------------------------------------
# Account types
# ---------------------------------------------------------------------------

# Known account types and whether they shelter growth from current taxation.
# "Other" is intentionally absent -- prompts.py asks explicitly in that case.
ACCOUNT_TYPE_TAX_TREATMENT: dict[str, TaxTreatment] = {
    "Roth IRA": TaxTreatment.TAX_ADVANTAGED,
    "Traditional IRA": TaxTreatment.TAX_ADVANTAGED,
    "Roth 401(k)": TaxTreatment.TAX_ADVANTAGED,
    "Traditional 401(k)": TaxTreatment.TAX_ADVANTAGED,
    "403(b)": TaxTreatment.TAX_ADVANTAGED,
    "457(b)": TaxTreatment.TAX_ADVANTAGED,
    "SEP IRA": TaxTreatment.TAX_ADVANTAGED,
    "SIMPLE IRA": TaxTreatment.TAX_ADVANTAGED,
    "HSA": TaxTreatment.TAX_ADVANTAGED,
    "Taxable Brokerage": TaxTreatment.TAXABLE,
}

OTHER_ACCOUNT_TYPE = "Other"

ACCOUNT_TYPE_CHOICES = [*ACCOUNT_TYPE_TAX_TREATMENT.keys(), OTHER_ACCOUNT_TYPE]


def infer_tax_treatment(account_type: str) -> TaxTreatment | None:
    """Look up the tax treatment for a known account type. Returns None for
    unrecognized types (including "Other"), signaling the caller must ask."""
    return ACCOUNT_TYPE_TAX_TREATMENT.get(account_type)
