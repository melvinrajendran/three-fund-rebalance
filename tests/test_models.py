from decimal import Decimal

import pytest

from three_fund_rebalance.models import (
    Account,
    FundType,
    Holding,
    TargetAllocation,
    TaxTreatment,
    TDFAllocation,
    Trade,
    to_cents,
)


def make_tdf(domestic=Decimal(60), intl=Decimal(20), bond=Decimal(20)) -> TDFAllocation:
    return TDFAllocation(
        domestic_equity_pct=domestic, international_equity_pct=intl, bond_pct=bond
    )


class TestTDFAllocation:
    def test_valid_allocation(self):
        tdf = make_tdf()
        assert tdf.domestic_equity_pct == Decimal(60)

    def test_rejects_sum_not_100(self):
        with pytest.raises(ValueError, match="must sum to 100"):
            TDFAllocation(
                domestic_equity_pct=Decimal(50),
                international_equity_pct=Decimal(20),
                bond_pct=Decimal(20),
            )

    def test_allows_small_rounding_slack(self):
        # 33.3 + 33.3 + 33.4 = 100.0 exactly, but check a case that's off by
        # a hair due to human-entered one-decimal percentages.
        tdf = TDFAllocation(
            domestic_equity_pct=Decimal("33.4"),
            international_equity_pct=Decimal("33.3"),
            bond_pct=Decimal("33.35"),
        )
        assert tdf.bond_pct == Decimal("33.35")

    def test_rejects_negative_component(self):
        with pytest.raises(ValueError, match="cannot be negative"):
            TDFAllocation(
                domestic_equity_pct=Decimal(-10),
                international_equity_pct=Decimal(90),
                bond_pct=Decimal(20),
            )


class TestHolding:
    def test_tdf_requires_allocation(self):
        with pytest.raises(ValueError, match="requires a tdf_allocation"):
            Holding(fund_type=FundType.TDF, name="Target 2050", balance=Decimal(100))

    def test_non_tdf_rejects_allocation(self):
        with pytest.raises(ValueError, match="Only TDF holdings"):
            Holding(
                fund_type=FundType.DOMESTIC_EQUITY,
                name="VTI",
                balance=Decimal(100),
                tdf_allocation=make_tdf(),
            )

    def test_negative_balance_rejected(self):
        with pytest.raises(ValueError, match="cannot be negative"):
            Holding(fund_type=FundType.DOMESTIC_EQUITY, name="VTI", balance=Decimal(-1))

    def test_fund_holding_requires_name(self):
        with pytest.raises(ValueError, match="non-empty name"):
            Holding(fund_type=FundType.DOMESTIC_EQUITY, name="  ", balance=Decimal(100))

    def test_cash_holding_allows_empty_name(self):
        holding = Holding(fund_type=FundType.CASH, name="", balance=Decimal(50))
        assert holding.balance == Decimal(50)

    def test_components_for_plain_fund(self):
        holding = Holding(fund_type=FundType.DOMESTIC_BOND, name="BND", balance=Decimal(1000))
        assert holding.bond_component() == Decimal(1000)
        assert holding.domestic_equity_component() == Decimal(0)
        assert holding.international_equity_component() == Decimal(0)

    def test_components_for_tdf_split_correctly(self):
        holding = Holding(
            fund_type=FundType.TDF,
            name="Target 2050",
            balance=Decimal(1000),
            tdf_allocation=make_tdf(domestic=Decimal(60), intl=Decimal(20), bond=Decimal(20)),
        )
        assert holding.domestic_equity_component() == Decimal(600)
        assert holding.international_equity_component() == Decimal(200)
        assert holding.bond_component() == Decimal(200)

    def test_cash_has_no_components(self):
        holding = Holding(fund_type=FundType.CASH, name="", balance=Decimal(500))
        assert holding.domestic_equity_component() == Decimal(0)
        assert holding.international_equity_component() == Decimal(0)
        assert holding.bond_component() == Decimal(0)


class TestAccount:
    def test_total_value_sums_holdings(self):
        account = Account(
            account_type="Roth IRA",
            name="My Roth",
            tax_treatment=TaxTreatment.TAX_ADVANTAGED,
            holdings=[
                Holding(fund_type=FundType.DOMESTIC_EQUITY, name="VTI", balance=Decimal(100)),
                Holding(fund_type=FundType.DOMESTIC_BOND, name="BND", balance=Decimal(50)),
            ],
        )
        assert account.total_value() == Decimal(150)

    def test_rejects_duplicate_fund_type(self):
        with pytest.raises(ValueError, match="more than one"):
            Account(
                account_type="Roth IRA",
                name="My Roth",
                tax_treatment=TaxTreatment.TAX_ADVANTAGED,
                holdings=[
                    Holding(fund_type=FundType.DOMESTIC_EQUITY, name="VTI", balance=Decimal(100)),
                    Holding(fund_type=FundType.DOMESTIC_EQUITY, name="VOO", balance=Decimal(50)),
                ],
            )

    def test_rejects_empty_name(self):
        with pytest.raises(ValueError, match="cannot be empty"):
            Account(account_type="Roth IRA", name="", tax_treatment=TaxTreatment.TAX_ADVANTAGED)

    def test_cash_balance_defaults_to_zero(self):
        account = Account(
            account_type="Taxable Brokerage", name="Brokerage", tax_treatment=TaxTreatment.TAXABLE
        )
        assert account.cash_balance() == Decimal(0)

    def test_cash_balance_reads_cash_holding(self):
        account = Account(
            account_type="Taxable Brokerage",
            name="Brokerage",
            tax_treatment=TaxTreatment.TAXABLE,
            holdings=[Holding(fund_type=FundType.CASH, name="", balance=Decimal(250))],
        )
        assert account.cash_balance() == Decimal(250)

    def test_is_tax_advantaged(self):
        taxable = Account(
            account_type="Taxable Brokerage", name="B", tax_treatment=TaxTreatment.TAXABLE
        )
        sheltered = Account(
            account_type="Roth IRA", name="R", tax_treatment=TaxTreatment.TAX_ADVANTAGED
        )
        assert not taxable.is_tax_advantaged()
        assert sheltered.is_tax_advantaged()

    def test_get_holding_returns_none_when_absent(self):
        account = Account(
            account_type="Roth IRA", name="R", tax_treatment=TaxTreatment.TAX_ADVANTAGED
        )
        assert account.get_holding(FundType.DOMESTIC_BOND) is None


class TestTargetAllocation:
    def test_valid(self):
        target = TargetAllocation(
            domestic_equity_pct=Decimal(50),
            international_equity_pct=Decimal(30),
            bond_pct=Decimal(20),
        )
        assert target.bond_pct == Decimal(20)

    def test_rejects_sum_not_100(self):
        with pytest.raises(ValueError, match="must sum to 100"):
            TargetAllocation(
                domestic_equity_pct=Decimal(50),
                international_equity_pct=Decimal(30),
                bond_pct=Decimal(30),
            )

    def test_rejects_negative_component(self):
        with pytest.raises(ValueError, match="cannot be negative"):
            TargetAllocation(
                domestic_equity_pct=Decimal(-10),
                international_equity_pct=Decimal(90),
                bond_pct=Decimal(20),
            )


class TestTrade:
    def test_rejects_invalid_action(self):
        with pytest.raises(ValueError, match="'buy' or 'sell'"):
            Trade(
                account_name="A",
                fund_type=FundType.DOMESTIC_EQUITY,
                fund_name="VTI",
                action="exchange",
                amount=Decimal(100),
            )

    def test_rejects_non_positive_amount(self):
        with pytest.raises(ValueError, match="must be positive"):
            Trade(
                account_name="A",
                fund_type=FundType.DOMESTIC_EQUITY,
                fund_name="VTI",
                action="buy",
                amount=Decimal(0),
            )


class TestToCents:
    def test_rounds_half_up(self):
        assert to_cents(Decimal("1.005")) == Decimal("1.01")
        assert to_cents(Decimal("1.004")) == Decimal("1.00")
