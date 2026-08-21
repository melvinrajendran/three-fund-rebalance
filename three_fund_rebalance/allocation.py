"""Turns a user's stock and bond target plus VT's U.S. weighting into a
concrete three-way TargetAllocation (U.S. stocks / international stocks /
bonds), and turns that into whole-portfolio dollar targets."""

from __future__ import annotations

from decimal import Decimal

from three_fund_rebalance.models import PERCENT_SUM_TOLERANCE, TargetAllocation


def compute_target_allocation(
    stock_pct: Decimal, bond_pct: Decimal, vt_us_pct: Decimal
) -> TargetAllocation:
    """Divide `stock_pct` into U.S. and international using VT's weighting.

    `international_stock_pct` is computed as `stock_pct - us_stock_pct`
    (rather than independently as `stock_pct * (100 - vt_us_pct) / 100`) so the three
    resulting percentages always sum to exactly `stock_pct + bond_pct`,
    regardless of any rounding in the division above.
    """
    total = stock_pct + bond_pct
    if abs(total - Decimal(100)) > PERCENT_SUM_TOLERANCE:
        raise ValueError(f"stock_pct + bond_pct must sum to 100 (got {total})")
    if not (Decimal(0) <= vt_us_pct <= Decimal(100)):
        raise ValueError(f"vt_us_pct must be between 0 and 100 (got {vt_us_pct})")

    us_stock_pct = stock_pct * vt_us_pct / Decimal(100)
    international_stock_pct = stock_pct - us_stock_pct
    return TargetAllocation(
        us_stock_pct=us_stock_pct,
        international_stock_pct=international_stock_pct,
        bond_pct=bond_pct,
    )


def target_dollar_amounts(
    target: TargetAllocation, total_portfolio_value: Decimal
) -> dict[str, Decimal]:
    """Convert a percentage TargetAllocation into whole-portfolio dollar
    amounts. Keyed by the same names as TargetAllocation's fields."""
    return {
        "us_stock": total_portfolio_value * target.us_stock_pct / Decimal(100),
        "international_stock": total_portfolio_value
        * target.international_stock_pct
        / Decimal(100),
        "bond": total_portfolio_value * target.bond_pct / Decimal(100),
    }
