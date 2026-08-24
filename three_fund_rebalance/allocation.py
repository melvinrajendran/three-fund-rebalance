"""Turns a user's stock and bond target plus VT's U.S. allocation into a
concrete three-way TargetAllocation (U.S. stocks / international stocks /
bonds), and turns that into whole-portfolio dollar targets."""

from __future__ import annotations

from decimal import Decimal

from three_fund_rebalance.models import PERCENT_SUM_TOLERANCE, FundType, TargetAllocation

#: The key each asset class is known by in the dicts of dollar amounts and
#: percentages that pass between allocation, the solver and the report.
#:
#: Deliberately *not* `FundType.value`. Those are the storage spellings that
#: go into config.json verbatim -- and there bonds are "us_bond", where every
#: in-memory dict here says "bond". Two of the three coincide, which is
#: precisely why this belongs in one place: a reader who notices the pattern
#: on the first two and infers it for the third is wrong, and re-typing the
#: mapping in each module that needs it is three chances to make that mistake
#: permanent. `rebalance` and `report` both import this rather than declaring
#: their own.
ASSET_CLASS_KEYS: dict[FundType, str] = {
    FundType.US_STOCK: "us_stock",
    FundType.INTERNATIONAL_STOCK: "international_stock",
    FundType.US_BOND: "bond",
}


def compute_target_allocation(
    stock_pct: Decimal, bond_pct: Decimal, vt_us_pct: Decimal
) -> TargetAllocation:
    """Divide `stock_pct` into U.S. and international using VT's allocation.

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


def target_percentages(target: TargetAllocation) -> dict[str, Decimal]:
    """A TargetAllocation as a plain mapping, keyed by the names the rest of
    the program uses for the three asset classes."""
    return {
        ASSET_CLASS_KEYS[FundType.US_STOCK]: target.us_stock_pct,
        ASSET_CLASS_KEYS[FundType.INTERNATIONAL_STOCK]: target.international_stock_pct,
        ASSET_CLASS_KEYS[FundType.US_BOND]: target.bond_pct,
    }


def target_dollar_amounts(
    target: TargetAllocation, total_portfolio_value: Decimal
) -> dict[str, Decimal]:
    """Convert a percentage TargetAllocation into whole-portfolio dollar
    amounts. Keyed by the same names as TargetAllocation's fields."""
    return {
        key: total_portfolio_value * pct / Decimal(100)
        for key, pct in target_percentages(target).items()
    }


def effective_band_points(
    target: TargetAllocation,
    band_pct: Decimal,
    relative_band_pct: Decimal | None = None,
) -> dict[str, Decimal]:
    """How far each asset class may drift from its target, in percentage
    points of the whole portfolio, before it is worth correcting.

    Two rules, and an asset class has to satisfy both -- so the **tighter**
    of the two is what binds, which is the 5/25 rule. `band_pct` is the
    absolute band, in points of the portfolio; `relative_band_pct` is the
    relative band, a share of the class's own target, so it scales with the
    target where the absolute one does not. Those are the names the prompts,
    the report and the saved keys all use.

    Neither alone is right for all three classes. Five points is a quarter of
    a 20% bond sleeve and is far too loose for a 5% one -- 5 points below a
    5% target is zero bonds, which no band should tolerate. Twenty-five
    percent of a 58.8% U.S. target is 14.7 points, which is far too loose for
    the class that dominates the portfolio. Taking the lesser gives the small
    targets the relative rule and the large ones the absolute cap; the two
    cross at a 20% target, where both come to 5 points, which is why the rule
    is often stated as "5 points at 20% and above, 25% relative below".

    `relative_band_pct` of `None` means the relative rule was never
    configured and only `band_pct` applies -- distinct from `0`, which like a
    `band_pct` of `0` means no drift is tolerated at all.
    """
    if band_pct < 0:
        raise ValueError(f"band_pct cannot be negative (got {band_pct})")
    if relative_band_pct is not None and relative_band_pct < 0:
        raise ValueError(f"relative_band_pct cannot be negative (got {relative_band_pct})")
    return {
        key: band_pct
        if relative_band_pct is None
        else min(band_pct, target_pct * relative_band_pct / Decimal(100))
        for key, target_pct in target_percentages(target).items()
    }


def target_dollar_bounds(
    target: TargetAllocation,
    total_portfolio_value: Decimal,
    band_pct: Decimal,
    relative_band_pct: Decimal | None = None,
) -> dict[str, tuple[Decimal, Decimal]]:
    """The dollar range each asset class may occupy: its target give or take
    whatever `effective_band_points` allows it, converted to dollars.

    Both edges are clamped to what a portfolio can actually hold. A 2% target
    with a 5-point band would otherwise ask for at least -3%, and the LP
    reads these as real bounds. With a band of 0 both edges collapse onto the
    target, which is the exact-target behavior these replaced.
    """
    amounts = target_dollar_amounts(target, total_portfolio_value)
    points = effective_band_points(target, band_pct, relative_band_pct)
    return {
        key: (
            max(Decimal(0), amount - total_portfolio_value * points[key] / Decimal(100)),
            min(total_portfolio_value, amount + total_portfolio_value * points[key] / Decimal(100)),
        )
        for key, amount in amounts.items()
    }
