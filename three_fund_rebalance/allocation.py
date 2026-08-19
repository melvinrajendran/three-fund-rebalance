"""Turns a user's stock/bond target plus VT's US/ex-US weighting into a
concrete three-way TargetAllocation (domestic equity / international equity
/ bonds), and turns that into whole-portfolio dollar targets."""

from __future__ import annotations

from decimal import Decimal

from three_fund_rebalance.models import PERCENT_SUM_TOLERANCE, TargetAllocation


def compute_target_allocation(
    stock_pct: Decimal, bond_pct: Decimal, vt_us_pct: Decimal
) -> TargetAllocation:
    """Split `stock_pct` into domestic/international using VT's weighting.

    `international_pct` is computed as `stock_pct - domestic_pct` (rather
    than independently as `stock_pct * (100 - vt_us_pct) / 100`) so the three
    resulting percentages always sum to exactly `stock_pct + bond_pct`,
    regardless of any rounding in the division above.
    """
    total = stock_pct + bond_pct
    if abs(total - Decimal(100)) > PERCENT_SUM_TOLERANCE:
        raise ValueError(f"stock_pct + bond_pct must sum to 100 (got {total})")
    if not (Decimal(0) <= vt_us_pct <= Decimal(100)):
        raise ValueError(f"vt_us_pct must be between 0 and 100 (got {vt_us_pct})")

    domestic_equity_pct = stock_pct * vt_us_pct / Decimal(100)
    international_equity_pct = stock_pct - domestic_equity_pct
    return TargetAllocation(
        domestic_equity_pct=domestic_equity_pct,
        international_equity_pct=international_equity_pct,
        bond_pct=bond_pct,
    )


def target_dollar_amounts(
    target: TargetAllocation, total_portfolio_value: Decimal
) -> dict[str, Decimal]:
    """Convert a percentage TargetAllocation into whole-portfolio dollar
    amounts. Keyed by the same names as TargetAllocation's fields."""
    return {
        "domestic_equity": total_portfolio_value * target.domestic_equity_pct / Decimal(100),
        "international_equity": total_portfolio_value
        * target.international_equity_pct
        / Decimal(100),
        "bond": total_portfolio_value * target.bond_pct / Decimal(100),
    }
