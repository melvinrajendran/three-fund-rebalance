"""Constants and defaults shared across the package."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from three_fund_rebalance.models import TaxTreatment

# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------

# 2 renamed the fund types and the per-holding fields to the same words the
# CLI puts on screen (us_stock/international_stock/us_bond/target_date, and
# `value` for a holding's dollar amount). 3 split the single `tax_advantaged`
# treatment into `tax_deferred` and `tax_free`, and added the rebalancing
# band. Older files are still read -- persistence._upgrade_v1 and
# _upgrade_v2 translate them on load -- and are rewritten at the current
# version the next time the user saves.
SCHEMA_VERSION = 3
DEFAULT_CONFIG_PATH = Path.home() / ".three_fund_rebalance" / "config.json"

# ---------------------------------------------------------------------------
# Trading
# ---------------------------------------------------------------------------

# Fidelity's fractional-share ("Stocks by the Slice") minimum is $1, and it
# applies the same way in a Roth IRA as in a taxable brokerage account, so
# the lesser of the two is $1. Trades smaller than this are not actionable
# and are dropped from the plan rather than shown as noise.
# https://www.fidelity.com/trading/fractional-shares
MIN_TRADE_DOLLARS = Decimal("1.00")

# ---------------------------------------------------------------------------
# Rebalancing band
# ---------------------------------------------------------------------------

# How far an asset class may drift from its target before it is worth
# correcting, in percentage points of the whole portfolio. Trading to an
# exact target means every drift, however small, generates trades -- and in a
# taxable account those trades cost real money to save a rounding error. The
# long-standing Bogleheads convention is a 5-percentage-point band, so that
# is the suggested default; 0 restores exact-target behavior.
# https://www.bogleheads.org/wiki/Rebalancing
DEFAULT_REBALANCE_BAND_PCT = Decimal(5)

# ---------------------------------------------------------------------------
# VT (Vanguard Total World Stock ETF) US / ex-US allocation
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

# Known account types and how each is taxed. The split between the two
# shelters is what decides where bonds go: contributions deducted now and
# taxed on withdrawal are TAX_DEFERRED, qualified withdrawals that are never
# taxed are TAX_FREE. "Other" is intentionally absent -- prompts.py asks
# explicitly in that case.
ACCOUNT_TYPE_TAX_TREATMENT: dict[str, TaxTreatment] = {
    "Roth IRA": TaxTreatment.TAX_FREE,
    "Traditional IRA": TaxTreatment.TAX_DEFERRED,
    "Roth 401(k)": TaxTreatment.TAX_FREE,
    "Traditional 401(k)": TaxTreatment.TAX_DEFERRED,
    "403(b)": TaxTreatment.TAX_DEFERRED,
    "457(b)": TaxTreatment.TAX_DEFERRED,
    "SEP IRA": TaxTreatment.TAX_DEFERRED,
    "SIMPLE IRA": TaxTreatment.TAX_DEFERRED,
    "HSA": TaxTreatment.TAX_FREE,
    "Taxable Brokerage": TaxTreatment.TAXABLE,
}

# Longest account nickname accepted. A nickname is a label the user makes up,
# so unlike a fund's real name it can fairly be bounded -- and it is what
# makes the account headings run past the page width. Generous enough for the
# descriptive names brokerages themselves use ("Fidelity Individual Brokerage
# Account" is 37). Only enforced at the prompt: a longer name in a config file
# written by an older version still loads.
MAX_ACCOUNT_NAME_LENGTH = 40

OTHER_ACCOUNT_TYPE = "Other"

ACCOUNT_TYPE_CHOICES = [*ACCOUNT_TYPE_TAX_TREATMENT.keys(), OTHER_ACCOUNT_TYPE]


def infer_tax_treatment(account_type: str) -> TaxTreatment | None:
    """Look up the tax treatment for a known account type. Returns None for
    unrecognized types (including "Other"), signaling the caller must ask."""
    return ACCOUNT_TYPE_TAX_TREATMENT.get(account_type)
