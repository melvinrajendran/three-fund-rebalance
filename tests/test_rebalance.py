from decimal import Decimal

import pytest

from three_fund_rebalance.models import (
    Account,
    FundType,
    Holding,
    TargetAllocation,
    TaxTreatment,
    TDFAllocation,
)
from three_fund_rebalance.rebalance import RebalanceError, compute_trades


def holding(fund_type, name, balance, tdf=None):
    return Holding(fund_type=fund_type, name=name, balance=Decimal(balance), tdf_allocation=tdf)


def account(account_type, name, tax_treatment, holdings):
    return Account(account_type=account_type, name=name, tax_treatment=tax_treatment, holdings=holdings)


def target(domestic, intl, bond):
    return TargetAllocation(
        domestic_equity_pct=Decimal(domestic),
        international_equity_pct=Decimal(intl),
        bond_pct=Decimal(bond),
    )


def trades_by_key(result):
    return {(t.account_name, t.fund_name): (t.action, t.amount) for t in result.trades}


class TestBasicRebalance:
    def test_already_balanced_produces_no_trades(self):
        accounts = [
            account(
                "Roth IRA",
                "Roth",
                TaxTreatment.TAX_ADVANTAGED,
                [
                    holding(FundType.DOMESTIC_EQUITY, "VTI", 600),
                    holding(FundType.INTERNATIONAL_EQUITY, "VXUS", 200),
                    holding(FundType.DOMESTIC_BOND, "BND", 200),
                ],
            )
        ]
        result = compute_trades(accounts, target(60, 20, 20))
        assert result.trades == []
        assert result.warnings == []
        assert result.taxable_bond_dollars == Decimal(0)

    def test_single_account_needs_full_reallocation(self):
        accounts = [
            account(
                "Roth IRA",
                "Roth",
                TaxTreatment.TAX_ADVANTAGED,
                [
                    holding(FundType.DOMESTIC_EQUITY, "VTI", 1000),
                    holding(FundType.INTERNATIONAL_EQUITY, "VXUS", 0),
                    holding(FundType.DOMESTIC_BOND, "BND", 0),
                ],
            )
        ]
        result = compute_trades(accounts, target(60, 20, 20))
        trades = trades_by_key(result)
        assert trades[("Roth", "VTI")] == ("sell", Decimal("400.00"))
        assert trades[("Roth", "VXUS")] == ("buy", Decimal("200.00"))
        assert trades[("Roth", "BND")] == ("buy", Decimal("200.00"))
        assert result.taxable_bond_dollars == Decimal(0)

    def test_empty_accounts_returns_empty_result(self):
        result = compute_trades([], target(60, 20, 20))
        assert result.trades == []
        assert result.warnings == []

    def test_zero_value_accounts_return_no_trades(self):
        accounts = [account("Roth IRA", "Roth", TaxTreatment.TAX_ADVANTAGED, [])]
        result = compute_trades(accounts, target(60, 20, 20))
        assert result.trades == []

    def test_empty_account_alongside_a_populated_one_is_skipped_cleanly(self):
        empty_account = account("Roth IRA", "EmptyRoth", TaxTreatment.TAX_ADVANTAGED, [])
        populated_account = account(
            "Traditional IRA", "TradIRA", TaxTreatment.TAX_ADVANTAGED,
            [
                holding(FundType.DOMESTIC_EQUITY, "VTI", 1000),
                holding(FundType.DOMESTIC_BOND, "BND", 0),
            ],
        )
        result = compute_trades([empty_account, populated_account], target(50, 0, 50))
        trades = trades_by_key(result)
        assert trades[("TradIRA", "VTI")] == ("sell", Decimal("500.00"))
        assert trades[("TradIRA", "BND")] == ("buy", Decimal("500.00"))
        assert not any(t.account_name == "EmptyRoth" for t in result.trades)


class TestSolverFailure:
    def test_solver_failure_is_wrapped_as_rebalance_error(self, monkeypatch):
        import three_fund_rebalance.rebalance as rebalance_module

        class FakeFailedResult:
            success = False
            message = "simulated infeasibility"

        monkeypatch.setattr(rebalance_module, "linprog", lambda **kwargs: FakeFailedResult())
        accounts = [
            account(
                "Roth IRA", "Roth", TaxTreatment.TAX_ADVANTAGED,
                [holding(FundType.DOMESTIC_EQUITY, "VTI", 100)],
            )
        ]
        with pytest.raises(RebalanceError, match="Could not find a feasible rebalance"):
            compute_trades(accounts, target(100, 0, 0))


class TestValidation:
    def test_duplicate_account_names_rejected(self):
        accounts = [
            account(
                "Roth IRA", "Same", TaxTreatment.TAX_ADVANTAGED,
                [holding(FundType.DOMESTIC_EQUITY, "VTI", 100)],
            ),
            account(
                "Traditional IRA", "Same", TaxTreatment.TAX_ADVANTAGED,
                [holding(FundType.DOMESTIC_EQUITY, "VTI", 100)],
            ),
        ]
        with pytest.raises(RebalanceError, match="must be unique"):
            compute_trades(accounts, target(100, 0, 0))

    def test_cash_with_no_fund_holdings_rejected(self):
        accounts = [
            account(
                "Taxable Brokerage", "Brokerage", TaxTreatment.TAXABLE,
                [holding(FundType.CASH, "", 500)],
            )
        ]
        with pytest.raises(RebalanceError, match="no fund holdings declared"):
            compute_trades(accounts, target(100, 0, 0))

    def test_infeasible_bond_target_raises_clear_error(self):
        # No account declares a bond-capable slot anywhere.
        accounts = [
            account(
                "Taxable Brokerage", "Brokerage", TaxTreatment.TAXABLE,
                [holding(FundType.DOMESTIC_EQUITY, "VTI", 10_000)],
            )
        ]
        with pytest.raises(RebalanceError, match="bond"):
            compute_trades(accounts, target(50, 0, 50))


class TestBondsPreferTaxAdvantaged:
    def test_bonds_fill_tax_advantaged_before_taxable_when_room_exists(self):
        tax_adv_1 = account(
            "Roth IRA", "Roth1", TaxTreatment.TAX_ADVANTAGED,
            [holding(FundType.DOMESTIC_EQUITY, "VTI", 2000), holding(FundType.DOMESTIC_BOND, "BND", 0)],
        )
        tax_adv_2 = account(
            "Traditional 401(k)", "401k", TaxTreatment.TAX_ADVANTAGED,
            [holding(FundType.DOMESTIC_EQUITY, "VTI", 3000), holding(FundType.DOMESTIC_BOND, "BND", 0)],
        )
        taxable = account(
            "Taxable Brokerage", "Brokerage", TaxTreatment.TAXABLE,
            [holding(FundType.DOMESTIC_EQUITY, "VTI", 5000), holding(FundType.DOMESTIC_BOND, "BND", 0)],
        )
        result = compute_trades([tax_adv_1, tax_adv_2, taxable], target(60, 0, 40))

        assert result.taxable_bond_dollars == Decimal(0)
        assert result.warnings == []
        taxable_bond_trade = [
            t for t in result.trades if t.account_name == "Brokerage" and t.fund_name == "BND"
        ]
        assert taxable_bond_trade == []

        bond_bought = sum(
            t.amount for t in result.trades if t.fund_name == "BND" and t.action == "buy"
        )
        assert bond_bought == Decimal("4000.00")
        domestic_total = (
            tax_adv_1.total_value() + tax_adv_2.total_value() + taxable.total_value()
        )
        assert domestic_total == Decimal(10_000)

    def test_bonds_overflow_to_taxable_when_tax_advantaged_insufficient(self):
        small_roth = account(
            "Roth IRA", "Roth", TaxTreatment.TAX_ADVANTAGED,
            [holding(FundType.DOMESTIC_EQUITY, "VTI", 100), holding(FundType.DOMESTIC_BOND, "BND", 0)],
        )
        big_taxable = account(
            "Taxable Brokerage", "Brokerage", TaxTreatment.TAXABLE,
            [holding(FundType.DOMESTIC_EQUITY, "VTI", 9900), holding(FundType.DOMESTIC_BOND, "BND", 0)],
        )
        # domestic 60 / bond 40 on a $10,000 portfolio -> $4,000 bonds needed,
        # but tax-advantaged capacity is only $100.
        result = compute_trades([small_roth, big_taxable], target(60, 0, 40))

        assert result.taxable_bond_dollars == Decimal("3900.00")
        assert len(result.warnings) == 1
        assert "3,900.00" in result.warnings[0] or "3900" in result.warnings[0]

        roth_bond = next(t for t in result.trades if t.account_name == "Roth" and t.fund_name == "BND")
        assert roth_bond.action == "buy"
        assert roth_bond.amount == Decimal("100.00")

        taxable_bond = next(
            t for t in result.trades if t.account_name == "Brokerage" and t.fund_name == "BND"
        )
        assert taxable_bond.action == "buy"
        assert taxable_bond.amount == Decimal("3900.00")


class TestTDF:
    def test_tdf_only_account_already_balanced(self):
        tdf_alloc = TDFAllocation(
            domestic_equity_pct=Decimal(60), international_equity_pct=Decimal(20), bond_pct=Decimal(20)
        )
        accounts = [
            account(
                "Roth 401(k)", "401k", TaxTreatment.TAX_ADVANTAGED,
                [holding(FundType.TDF, "Target 2050", 10_000, tdf=tdf_alloc)],
            )
        ]
        result = compute_trades(accounts, target(60, 20, 20))
        assert result.trades == []

    def test_tdf_plus_individual_fund_solved_correctly(self):
        tdf_alloc = TDFAllocation(
            domestic_equity_pct=Decimal(60), international_equity_pct=Decimal(20), bond_pct=Decimal(20)
        )
        accounts = [
            account(
                "Roth 401(k)", "Mixed401k", TaxTreatment.TAX_ADVANTAGED,
                [
                    holding(FundType.DOMESTIC_EQUITY, "VTI", 7000),
                    holding(FundType.TDF, "TargetFund", 3000, tdf=tdf_alloc),
                ],
            )
        ]
        # intl and bonds can only come from the TDF, forcing it to $5,000
        # (since 20% of $5,000 = $1,000 = each of the intl/bond targets).
        result = compute_trades(accounts, target(80, 10, 10))
        trades = trades_by_key(result)
        assert trades[("Mixed401k", "VTI")] == ("sell", Decimal("2000.00"))
        assert trades[("Mixed401k", "TargetFund")] == ("buy", Decimal("2000.00"))

    def test_tdf_in_taxable_account_counts_toward_taxable_bonds(self):
        tdf_alloc = TDFAllocation(
            domestic_equity_pct=Decimal(60), international_equity_pct=Decimal(20), bond_pct=Decimal(20)
        )
        accounts = [
            account(
                "Taxable Brokerage", "Brokerage", TaxTreatment.TAXABLE,
                [holding(FundType.TDF, "Target 2050", 10_000, tdf=tdf_alloc)],
            )
        ]
        result = compute_trades(accounts, target(60, 20, 20))
        # Only one slot exists in the whole portfolio, so nothing can move --
        # the $2,000 bond sleeve inside the taxable TDF is unavoidable.
        assert result.trades == []
        assert result.taxable_bond_dollars == Decimal("2000.00")
        assert len(result.warnings) == 1


class TestCashInvestment:
    def test_uninvested_cash_gets_fully_invested(self):
        accounts = [
            account(
                "Taxable Brokerage", "Brokerage", TaxTreatment.TAXABLE,
                [
                    holding(FundType.DOMESTIC_EQUITY, "VTI", 0),
                    holding(FundType.INTERNATIONAL_EQUITY, "VXUS", 0),
                    holding(FundType.CASH, "", 1000),
                ],
            )
        ]
        result = compute_trades(accounts, target(75, 25, 0))
        trades = trades_by_key(result)
        assert trades[("Brokerage", "VTI")] == ("buy", Decimal("750.00"))
        assert trades[("Brokerage", "VXUS")] == ("buy", Decimal("250.00"))
        assert not any(t.fund_type == FundType.CASH for t in result.trades)
        assert sum(t.amount for t in result.trades) == Decimal("1000.00")


class TestPerAccountConservation:
    """Rounding each solved slot to cents independently can leave an account
    a cent off its real balance, which yields an unfillable recommendation
    (e.g. 'buy $5,000.01' against exactly $5,000.00 of cash). Every trade set
    must leave each account's holdings summing to exactly its total."""

    def assert_conserved(self, accounts, result):
        deltas: dict[str, Decimal] = {}
        for t in result.trades:
            signed = t.amount if t.action == "buy" else -t.amount
            deltas[t.account_name] = deltas.get(t.account_name, Decimal(0)) + signed
        for account in accounts:
            net = deltas.get(account.name, Decimal(0))
            expected = account.cash_balance()  # cash is spent, so buys exceed sells by that much
            assert net == expected, (
                f"{account.name}: trades net {net}, expected {expected} "
                f"(account total {account.total_value()})"
            )

    def test_three_account_portfolio_conserves_each_account_total(self):
        # This is the scenario that originally produced $5,000.01 of buys
        # against $5,000.00 of cash: an 80/20 target with VT's 61.9% US split
        # yields repeating-decimal dollar targets.
        accounts = [
            account(
                "Roth IRA", "Fidelity Roth", TaxTreatment.TAX_ADVANTAGED,
                [
                    holding(FundType.DOMESTIC_EQUITY, "VTI", 20_000),
                    holding(FundType.INTERNATIONAL_EQUITY, "VXUS", 10_000),
                    holding(FundType.DOMESTIC_BOND, "BND", 0),
                ],
            ),
            account(
                "Traditional 401(k)", "Acme 401k", TaxTreatment.TAX_ADVANTAGED,
                [
                    holding(FundType.DOMESTIC_EQUITY, "VTI", 40_000),
                    holding(FundType.DOMESTIC_BOND, "BND", 10_000),
                ],
            ),
            account(
                "Taxable Brokerage", "Fidelity Brokerage", TaxTreatment.TAXABLE,
                [
                    holding(FundType.DOMESTIC_EQUITY, "VTI", 25_000),
                    holding(FundType.INTERNATIONAL_EQUITY, "VXUS", 5_000),
                    holding(FundType.CASH, "", 5_000),
                ],
            ),
        ]
        result = compute_trades(
            accounts,
            TargetAllocation(
                domestic_equity_pct=Decimal("49.52"),
                international_equity_pct=Decimal("30.48"),
                bond_pct=Decimal(20),
            ),
        )
        self.assert_conserved(accounts, result)
        # The taxable account should spend exactly its cash, to the cent.
        taxable_buys = sum(
            t.amount for t in result.trades if t.account_name == "Fidelity Brokerage"
        )
        assert taxable_buys == Decimal("5000.00")

    def test_odd_cent_balances_still_conserve(self):
        accounts = [
            account(
                "Roth IRA", "Roth", TaxTreatment.TAX_ADVANTAGED,
                [
                    holding(FundType.DOMESTIC_EQUITY, "VTI", Decimal("3333.33")),
                    holding(FundType.INTERNATIONAL_EQUITY, "VXUS", Decimal("3333.33")),
                    holding(FundType.DOMESTIC_BOND, "BND", Decimal("3333.34")),
                ],
            )
        ]
        result = compute_trades(
            accounts,
            TargetAllocation(
                domestic_equity_pct=Decimal("49.52"),
                international_equity_pct=Decimal("30.48"),
                bond_pct=Decimal(20),
            ),
        )
        self.assert_conserved(accounts, result)

    def test_thirds_split_across_uneven_accounts_conserves(self):
        # A 1/3-each target over accounts whose totals don't divide evenly is
        # the classic largest-remainder stress case.
        accounts = [
            account(
                "Roth IRA", f"Acct{i}", TaxTreatment.TAX_ADVANTAGED,
                [
                    holding(FundType.DOMESTIC_EQUITY, "VTI", balance),
                    holding(FundType.INTERNATIONAL_EQUITY, "VXUS", 0),
                    holding(FundType.DOMESTIC_BOND, "BND", 0),
                ],
            )
            for i, balance in enumerate([Decimal("1000.01"), Decimal("2000.02"), Decimal("3000.07")])
        ]
        result = compute_trades(
            accounts,
            TargetAllocation(
                domestic_equity_pct=Decimal("33.34"),
                international_equity_pct=Decimal("33.33"),
                bond_pct=Decimal("33.33"),
            ),
        )
        self.assert_conserved(accounts, result)


class TestMinimumTradeThreshold:
    def test_sub_dollar_drift_is_not_traded(self):
        accounts = [
            account(
                "Roth IRA", "Roth", TaxTreatment.TAX_ADVANTAGED,
                [
                    holding(FundType.DOMESTIC_EQUITY, "VTI", Decimal("600.50")),
                    holding(FundType.INTERNATIONAL_EQUITY, "VXUS", Decimal("399.50")),
                ],
            )
        ]
        result = compute_trades(accounts, target(60, 40, 0))
        assert result.trades == []
