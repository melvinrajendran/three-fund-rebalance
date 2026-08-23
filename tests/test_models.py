from decimal import Decimal

import pytest

from three_fund_rebalance.models import (
    Account,
    FundType,
    Holding,
    TargetAllocation,
    TargetDateAllocation,
    TaxTreatment,
    Trade,
    to_cents,
)


def make_target_date(
    us_stock=Decimal(60), international=Decimal(20), bond=Decimal(20)
) -> TargetDateAllocation:
    return TargetDateAllocation(
        us_stock_pct=us_stock, international_stock_pct=international, bond_pct=bond
    )


class TestTargetDateAllocation:
    def test_valid_allocation(self):
        allocation = make_target_date()
        assert allocation.us_stock_pct == Decimal(60)

    def test_rejects_sum_not_100(self):
        with pytest.raises(ValueError, match="must sum to 100"):
            TargetDateAllocation(
                us_stock_pct=Decimal(50),
                international_stock_pct=Decimal(20),
                bond_pct=Decimal(20),
            )

    def test_allows_small_rounding_slack(self):
        # 33.3 + 33.3 + 33.4 = 100.0 exactly, but check a case that's off by
        # a hair due to human-entered one-decimal percentages.
        allocation = TargetDateAllocation(
            us_stock_pct=Decimal("33.4"),
            international_stock_pct=Decimal("33.3"),
            bond_pct=Decimal("33.35"),
        )
        assert allocation.bond_pct == Decimal("33.35")

    def test_rejects_negative_component(self):
        with pytest.raises(ValueError, match="cannot be negative"):
            TargetDateAllocation(
                us_stock_pct=Decimal(-10),
                international_stock_pct=Decimal(90),
                bond_pct=Decimal(20),
            )

    def test_fraction_of_normalizes_a_fact_sheet_that_does_not_sum_to_100(self):
        """The three sleeves have to come to exactly 1, whatever the fact
        sheet's own rounding did -- the solver reads them alongside the
        account's budget as hard equalities, and any gap between the two
        makes an ordinary portfolio infeasible rather than merely
        imprecise."""
        allocation = TargetDateAllocation(
            us_stock_pct=Decimal("64.0"),
            international_stock_pct=Decimal("34.3"),
            bond_pct=Decimal("1.6"),  # sums to 99.9
        )
        fractions = [
            allocation.fraction_of(FundType.US_STOCK),
            allocation.fraction_of(FundType.INTERNATIONAL_STOCK),
            allocation.fraction_of(FundType.US_BOND),
        ]
        # 1.0 exactly as floats, which is the form the solver reads them in
        # and the form the equalities have to agree in. As Decimals they are
        # 1 to within a division artifact some twenty orders of magnitude
        # below a cent -- against the tenth of a percent of the whole account
        # that dividing by 100 would have left unaccounted for.
        assert sum(float(fraction) for fraction in fractions) == 1.0
        assert abs(sum(fractions) - Decimal(1)) < Decimal("1e-20")
        assert fractions[0] == Decimal("64.0") / Decimal("99.9")

    def test_fraction_of_leaves_the_entered_percentages_alone(self):
        """A derived view, not a rewrite: prompts and the report echo the
        fund's own numbers back, so those have to survive as entered."""
        allocation = TargetDateAllocation(
            us_stock_pct=Decimal("64.0"),
            international_stock_pct=Decimal("34.3"),
            bond_pct=Decimal("1.6"),
        )
        assert allocation.us_stock_pct == Decimal("64.0")

    def test_fraction_of_a_class_the_fund_does_not_break_out_is_zero(self):
        assert make_target_date().fraction_of(FundType.CASH) == Decimal(0)


class TestHolding:
    def test_target_date_fund_requires_allocation(self):
        with pytest.raises(ValueError, match="requires a target_date_allocation"):
            Holding(fund_type=FundType.TARGET_DATE, name="Target 2050", value=Decimal(100))

    def test_non_target_date_fund_rejects_allocation(self):
        with pytest.raises(ValueError, match="Only target-date fund holdings"):
            Holding(
                fund_type=FundType.US_STOCK,
                name="VTI",
                value=Decimal(100),
                target_date_allocation=make_target_date(),
            )

    def test_negative_value_rejected(self):
        with pytest.raises(ValueError, match="cannot be negative"):
            Holding(fund_type=FundType.US_STOCK, name="VTI", value=Decimal(-1))

    def test_fund_holding_requires_name(self):
        with pytest.raises(ValueError, match="non-empty name"):
            Holding(fund_type=FundType.US_STOCK, name="  ", value=Decimal(100))

    def test_cash_holding_allows_empty_name(self):
        holding = Holding(fund_type=FundType.CASH, name="", value=Decimal(50))
        assert holding.value == Decimal(50)

    def test_components_for_plain_fund(self):
        holding = Holding(fund_type=FundType.US_BOND, name="BND", value=Decimal(1000))
        assert holding.bond_component() == Decimal(1000)
        assert holding.us_stock_component() == Decimal(0)
        assert holding.international_stock_component() == Decimal(0)

    def test_components_for_target_date_fund_divide_correctly(self):
        holding = Holding(
            fund_type=FundType.TARGET_DATE,
            name="Target 2050",
            value=Decimal(1000),
            target_date_allocation=make_target_date(us_stock=Decimal(60), international=Decimal(20), bond=Decimal(20)),
        )
        assert holding.us_stock_component() == Decimal(600)
        assert holding.international_stock_component() == Decimal(200)
        assert holding.bond_component() == Decimal(200)

    def test_cash_has_no_components(self):
        holding = Holding(fund_type=FundType.CASH, name="", value=Decimal(500))
        assert holding.us_stock_component() == Decimal(0)
        assert holding.international_stock_component() == Decimal(0)
        assert holding.bond_component() == Decimal(0)


class TestAccountHoldsOneKind:
    """An account holds either a target-date fund or individual funds."""

    def test_mixing_a_target_date_fund_with_individual_funds_is_rejected(self):
        with pytest.raises(ValueError, match="one or the other"):
            Account(
                account_type="Roth 401(k)",
                name="401k",
                tax_treatment=TaxTreatment.TAX_DEFERRED,
                holdings=[
                    Holding(fund_type=FundType.US_STOCK, name="VTI", value=Decimal(6000)),
                    Holding(
                        fund_type=FundType.TARGET_DATE,
                        name="Target 2050",
                        value=Decimal(3000),
                        target_date_allocation=make_target_date(),
                    ),
                ],
            )

    def test_the_message_names_the_account_and_the_funds_that_clashed(self):
        with pytest.raises(ValueError) as exc_info:
            Account(
                account_type="Roth 401(k)",
                name="Acme 401k",
                tax_treatment=TaxTreatment.TAX_DEFERRED,
                holdings=[
                    Holding(fund_type=FundType.US_BOND, name="BND", value=Decimal(1)),
                    Holding(
                        fund_type=FundType.TARGET_DATE,
                        name="Target 2050",
                        value=Decimal(1),
                        target_date_allocation=make_target_date(),
                    ),
                ],
            )
        message = str(exc_info.value)
        assert "Acme 401k" in message
        assert "target_date" in message and "us_bond" in message

    def test_cash_may_sit_alongside_either_kind(self):
        for funds in (
            [Holding(fund_type=FundType.US_STOCK, name="VTI", value=Decimal(1))],
            [Holding(
                fund_type=FundType.TARGET_DATE,
                name="Target 2050",
                value=Decimal(1),
                target_date_allocation=make_target_date(),
            )],
        ):
            built = Account(
                account_type="Roth IRA",
                name="Roth",
                tax_treatment=TaxTreatment.TAX_DEFERRED,
                holdings=[*funds, Holding(fund_type=FundType.CASH, name="", value=Decimal(5))],
            )
            assert built.available_cash() == Decimal(5)


class TestAccount:
    def test_total_value_sums_holdings(self):
        account = Account(
            account_type="Roth IRA",
            name="My Roth",
            tax_treatment=TaxTreatment.TAX_DEFERRED,
            holdings=[
                Holding(fund_type=FundType.US_STOCK, name="VTI", value=Decimal(100)),
                Holding(fund_type=FundType.US_BOND, name="BND", value=Decimal(50)),
            ],
        )
        assert account.total_value() == Decimal(150)

    def test_rejects_duplicate_fund_type(self):
        with pytest.raises(ValueError, match="more than one"):
            Account(
                account_type="Roth IRA",
                name="My Roth",
                tax_treatment=TaxTreatment.TAX_DEFERRED,
                holdings=[
                    Holding(fund_type=FundType.US_STOCK, name="VTI", value=Decimal(100)),
                    Holding(fund_type=FundType.US_STOCK, name="VOO", value=Decimal(50)),
                ],
            )

    def test_rejects_empty_name(self):
        with pytest.raises(ValueError, match="cannot be empty"):
            Account(account_type="Roth IRA", name="", tax_treatment=TaxTreatment.TAX_DEFERRED)

    def test_available_cash_defaults_to_zero(self):
        account = Account(
            account_type="Brokerage", name="Brokerage", tax_treatment=TaxTreatment.TAXABLE
        )
        assert account.available_cash() == Decimal(0)

    def test_available_cash_reads_cash_holding(self):
        account = Account(
            account_type="Brokerage",
            name="Brokerage",
            tax_treatment=TaxTreatment.TAXABLE,
            holdings=[Holding(fund_type=FundType.CASH, name="", value=Decimal(250))],
        )
        assert account.available_cash() == Decimal(250)

    def test_is_tax_advantaged(self):
        taxable = Account(
            account_type="Brokerage", name="B", tax_treatment=TaxTreatment.TAXABLE
        )
        sheltered = Account(
            account_type="Roth IRA", name="R", tax_treatment=TaxTreatment.TAX_DEFERRED
        )
        assert not taxable.is_tax_advantaged()
        assert sheltered.is_tax_advantaged()

    def test_get_holding_returns_none_when_absent(self):
        account = Account(
            account_type="Roth IRA", name="R", tax_treatment=TaxTreatment.TAX_DEFERRED
        )
        assert account.get_holding(FundType.US_BOND) is None


class TestTargetAllocation:
    def test_valid(self):
        target = TargetAllocation(
            us_stock_pct=Decimal(50),
            international_stock_pct=Decimal(30),
            bond_pct=Decimal(20),
        )
        assert target.bond_pct == Decimal(20)

    def test_rejects_sum_not_100(self):
        with pytest.raises(ValueError, match="must sum to 100"):
            TargetAllocation(
                us_stock_pct=Decimal(50),
                international_stock_pct=Decimal(30),
                bond_pct=Decimal(30),
            )

    def test_rejects_negative_component(self):
        with pytest.raises(ValueError, match="cannot be negative"):
            TargetAllocation(
                us_stock_pct=Decimal(-10),
                international_stock_pct=Decimal(90),
                bond_pct=Decimal(20),
            )


class TestTrade:
    def test_rejects_invalid_action(self):
        with pytest.raises(ValueError, match="'buy' or 'sell'"):
            Trade(
                account_name="A",
                fund_type=FundType.US_STOCK,
                fund_name="VTI",
                action="exchange",
                amount=Decimal(100),
            )

    def test_rejects_non_positive_amount(self):
        with pytest.raises(ValueError, match="must be positive"):
            Trade(
                account_name="A",
                fund_type=FundType.US_STOCK,
                fund_name="VTI",
                action="buy",
                amount=Decimal(0),
            )


class TestToCents:
    def test_rounds_half_up(self):
        assert to_cents(Decimal("1.005")) == Decimal("1.01")
        assert to_cents(Decimal("1.004")) == Decimal("1.00")
