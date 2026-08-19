from decimal import Decimal

import pytest

from three_fund_rebalance.allocation import compute_target_allocation, target_dollar_amounts
from three_fund_rebalance.models import TargetAllocation


class TestComputeTargetAllocation:
    def test_splits_stock_by_vt_weighting(self):
        target = compute_target_allocation(
            stock_pct=Decimal(80), bond_pct=Decimal(20), vt_us_pct=Decimal("61.9")
        )
        # 80% stock * 61.9% US = 49.52% domestic; international = 80 - 49.52 = 30.48
        assert target.domestic_equity_pct == Decimal("49.52")
        assert target.international_equity_pct == Decimal("30.48")
        assert target.bond_pct == Decimal(20)

    def test_percentages_always_sum_to_100(self):
        target = compute_target_allocation(
            stock_pct=Decimal(70), bond_pct=Decimal(30), vt_us_pct=Decimal("61.9")
        )
        total = target.domestic_equity_pct + target.international_equity_pct + target.bond_pct
        assert total == Decimal(100)

    def test_all_bonds(self):
        target = compute_target_allocation(
            stock_pct=Decimal(0), bond_pct=Decimal(100), vt_us_pct=Decimal("61.9")
        )
        assert target.domestic_equity_pct == Decimal(0)
        assert target.international_equity_pct == Decimal(0)
        assert target.bond_pct == Decimal(100)

    def test_all_stock(self):
        target = compute_target_allocation(
            stock_pct=Decimal(100), bond_pct=Decimal(0), vt_us_pct=Decimal(60)
        )
        assert target.domestic_equity_pct == Decimal(60)
        assert target.international_equity_pct == Decimal(40)

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
            domestic_equity_pct=Decimal(50),
            international_equity_pct=Decimal(30),
            bond_pct=Decimal(20),
        )
        amounts = target_dollar_amounts(target, Decimal(100_000))
        assert amounts["domestic_equity"] == Decimal(50_000)
        assert amounts["international_equity"] == Decimal(30_000)
        assert amounts["bond"] == Decimal(20_000)

    def test_amounts_sum_to_total(self):
        target = TargetAllocation(
            domestic_equity_pct=Decimal("49.52"),
            international_equity_pct=Decimal("30.48"),
            bond_pct=Decimal(20),
        )
        amounts = target_dollar_amounts(target, Decimal(37_500))
        assert sum(amounts.values()) == Decimal(37_500)
