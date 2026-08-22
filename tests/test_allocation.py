from decimal import Decimal

import pytest

from three_fund_rebalance.allocation import (
    compute_target_allocation,
    effective_band_points,
    target_dollar_amounts,
    target_dollar_bounds,
)
from three_fund_rebalance.models import TargetAllocation


class TestComputeTargetAllocation:
    def test_divides_stock_by_vt_weighting(self):
        target = compute_target_allocation(
            stock_pct=Decimal(80), bond_pct=Decimal(20), vt_us_pct=Decimal("61.9")
        )
        # 80% stock * 61.9% U.S. = 49.52% U.S.; international = 80 - 49.52 = 30.48
        assert target.us_stock_pct == Decimal("49.52")
        assert target.international_stock_pct == Decimal("30.48")
        assert target.bond_pct == Decimal(20)

    def test_percentages_always_sum_to_100(self):
        target = compute_target_allocation(
            stock_pct=Decimal(70), bond_pct=Decimal(30), vt_us_pct=Decimal("61.9")
        )
        total = target.us_stock_pct + target.international_stock_pct + target.bond_pct
        assert total == Decimal(100)

    def test_all_bonds(self):
        target = compute_target_allocation(
            stock_pct=Decimal(0), bond_pct=Decimal(100), vt_us_pct=Decimal("61.9")
        )
        assert target.us_stock_pct == Decimal(0)
        assert target.international_stock_pct == Decimal(0)
        assert target.bond_pct == Decimal(100)

    def test_all_stock(self):
        target = compute_target_allocation(
            stock_pct=Decimal(100), bond_pct=Decimal(0), vt_us_pct=Decimal(60)
        )
        assert target.us_stock_pct == Decimal(60)
        assert target.international_stock_pct == Decimal(40)

    def test_rejects_stock_bond_not_summing_to_100(self):
        with pytest.raises(ValueError, match="must sum to 100"):
            compute_target_allocation(
                stock_pct=Decimal(80), bond_pct=Decimal(30), vt_us_pct=Decimal(60)
            )

    def test_rejects_vt_us_pct_out_of_range(self):
        with pytest.raises(ValueError, match="between 0 and 100"):
            compute_target_allocation(
                stock_pct=Decimal(80), bond_pct=Decimal(20), vt_us_pct=Decimal(150)
            )


class TestTargetDollarAmounts:
    def test_converts_percentages_to_dollars(self):
        target = TargetAllocation(
            us_stock_pct=Decimal(50),
            international_stock_pct=Decimal(30),
            bond_pct=Decimal(20),
        )
        amounts = target_dollar_amounts(target, Decimal(100_000))
        assert amounts["us_stock"] == Decimal(50_000)
        assert amounts["international_stock"] == Decimal(30_000)
        assert amounts["bond"] == Decimal(20_000)

    def test_amounts_sum_to_total(self):
        target = TargetAllocation(
            us_stock_pct=Decimal("49.52"),
            international_stock_pct=Decimal("30.48"),
            bond_pct=Decimal(20),
        )
        amounts = target_dollar_amounts(target, Decimal(37_500))
        assert sum(amounts.values()) == Decimal(37_500)


class TestTargetDollarBounds:
    def _target(self):
        return TargetAllocation(
            us_stock_pct=Decimal(50), international_stock_pct=Decimal(30), bond_pct=Decimal(20)
        )

    def test_band_is_applied_in_points_of_the_whole_portfolio(self):
        """One number has to mean the same thing for every asset class, so
        the band is points of the portfolio rather than a share of each
        target: 5 points of $100,000 is $5,000 for all three."""
        bounds = target_dollar_bounds(self._target(), Decimal(100_000), Decimal(5))
        assert bounds["us_stock"] == (Decimal(45_000), Decimal(55_000))
        assert bounds["international_stock"] == (Decimal(25_000), Decimal(35_000))
        assert bounds["bond"] == (Decimal(15_000), Decimal(25_000))

    def test_a_band_of_zero_collapses_both_edges_onto_the_target(self):
        bounds = target_dollar_bounds(self._target(), Decimal(100_000), Decimal(0))
        amounts = target_dollar_amounts(self._target(), Decimal(100_000))
        for key, (low, high) in bounds.items():
            assert low == high == amounts[key]

    def test_edges_are_clamped_to_what_a_portfolio_can_actually_hold(self):
        """A 2% target with a 5-point band would otherwise ask for at least
        -3% of the portfolio, and the solver reads these as real bounds."""
        target = TargetAllocation(
            us_stock_pct=Decimal(98), international_stock_pct=Decimal(0), bond_pct=Decimal(2)
        )
        bounds = target_dollar_bounds(target, Decimal(100_000), Decimal(5))
        assert bounds["bond"] == (Decimal(0), Decimal(7_000))
        assert bounds["us_stock"] == (Decimal(93_000), Decimal(100_000))

    def test_a_negative_band_is_rejected(self):
        with pytest.raises(ValueError, match="cannot be negative"):
            target_dollar_bounds(self._target(), Decimal(100_000), Decimal(-1))


class TestEffectiveBandPoints:
    """The two halves of the 5/25 rule, and how they meet."""

    def _target(self, us, intl, bond):
        return TargetAllocation(
            us_stock_pct=Decimal(us),
            international_stock_pct=Decimal(intl),
            bond_pct=Decimal(bond),
        )

    def test_the_tighter_of_the_two_rules_binds(self):
        """A class has to satisfy both, so whichever runs out first decides.
        25% of a 58.8% target is 14.7 points, far looser than the absolute
        5 -- while 25% of a 5% target is 1.25 points, far tighter."""
        points = effective_band_points(
            self._target("58.8", "36.2", 5), Decimal(5), Decimal(25)
        )
        assert points["us_stock"] == Decimal(5)
        assert points["international_stock"] == Decimal(5)
        assert points["bond"] == Decimal("1.25")

    def test_the_two_rules_cross_at_a_twenty_percent_target(self):
        """Which is why the convention is usually stated as "5 points at 20%
        and above, 25% relative below" -- one rule, described twice."""
        points = effective_band_points(self._target(50, 30, 20), Decimal(5), Decimal(25))
        assert points["bond"] == Decimal(5)
        assert effective_band_points(
            self._target(50, 40, 10), Decimal(5), Decimal(25)
        )["bond"] == Decimal("2.5")

    def test_no_relative_rule_leaves_the_absolute_one_alone(self):
        points = effective_band_points(self._target("58.8", "36.2", 5), Decimal(5), None)
        assert set(points.values()) == {Decimal(5)}

    def test_zero_on_either_half_tolerates_no_drift(self):
        """Unlike None, which means the rule was never configured, zero is a
        real answer and it is the tightest one there is."""
        target = self._target(50, 30, 20)
        assert set(effective_band_points(target, Decimal(5), Decimal(0)).values()) == {Decimal(0)}
        assert set(effective_band_points(target, Decimal(0), Decimal(25)).values()) == {Decimal(0)}

    def test_a_negative_relative_band_is_rejected(self):
        with pytest.raises(ValueError, match="cannot be negative"):
            effective_band_points(self._target(50, 30, 20), Decimal(5), Decimal(-1))


class TestTargetDollarBoundsWithARelativeBand:
    def test_a_small_target_gets_a_band_the_absolute_rule_would_never_give_it(self):
        """5 points below a 5% target is zero bonds, which no band should
        call acceptable. A quarter of the target is 1.25 points."""
        target = TargetAllocation(
            us_stock_pct=Decimal("58.8"),
            international_stock_pct=Decimal("36.2"),
            bond_pct=Decimal(5),
        )
        assert target_dollar_bounds(target, Decimal(100_000), Decimal(5))["bond"] == (
            Decimal(0),
            Decimal(10_000),
        )
        assert target_dollar_bounds(target, Decimal(100_000), Decimal(5), Decimal(25))["bond"] == (
            Decimal(3_750),
            Decimal(6_250),
        )
