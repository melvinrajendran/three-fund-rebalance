from decimal import Decimal

import pytest

from three_fund_rebalance.models import (
    Account,
    FundType,
    Holding,
    TargetAllocation,
    TargetDateAllocation,
    TaxTreatment,
)
from three_fund_rebalance.rebalance import RebalanceError, compute_trades


def holding(fund_type, name, value, allocation=None):
    return Holding(
        fund_type=fund_type, name=name, value=Decimal(value), target_date_allocation=allocation
    )


def account(account_type, name, tax_treatment, holdings):
    return Account(account_type=account_type, name=name, tax_treatment=tax_treatment, holdings=holdings)


def target(us_stock, international, bond):
    return TargetAllocation(
        us_stock_pct=Decimal(us_stock),
        international_stock_pct=Decimal(international),
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
                    holding(FundType.US_STOCK, "VTI", 600),
                    holding(FundType.INTERNATIONAL_STOCK, "VXUS", 200),
                    holding(FundType.US_BOND, "BND", 200),
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
                    holding(FundType.US_STOCK, "VTI", 1000),
                    holding(FundType.INTERNATIONAL_STOCK, "VXUS", 0),
                    holding(FundType.US_BOND, "BND", 0),
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
                holding(FundType.US_STOCK, "VTI", 1000),
                holding(FundType.US_BOND, "BND", 0),
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
                [holding(FundType.US_STOCK, "VTI", 100)],
            )
        ]
        with pytest.raises(RebalanceError, match="Could not find a feasible rebalance"):
            compute_trades(accounts, target(100, 0, 0))


class TestValidation:
    def test_duplicate_account_names_rejected(self):
        accounts = [
            account(
                "Roth IRA", "Same", TaxTreatment.TAX_ADVANTAGED,
                [holding(FundType.US_STOCK, "VTI", 100)],
            ),
            account(
                "Traditional IRA", "Same", TaxTreatment.TAX_ADVANTAGED,
                [holding(FundType.US_STOCK, "VTI", 100)],
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
                [holding(FundType.US_STOCK, "VTI", 10_000)],
            )
        ]
        with pytest.raises(RebalanceError, match="bond"):
            compute_trades(accounts, target(50, 0, 50))


    def test_target_below_what_a_single_fund_account_forces_raises_clear_error(self):
        """A pinned account sets a floor as well as a ceiling: this fund is
        20% bonds and the account holds nothing else, so the portfolio cannot
        hold less than $2,000 of bonds however the rest is arranged."""
        target_date_alloc = TargetDateAllocation(
            us_stock_pct=Decimal(60), international_stock_pct=Decimal(20), bond_pct=Decimal(20)
        )
        accounts = [
            account(
                "Roth 401(k)", "401k", TaxTreatment.TAX_ADVANTAGED,
                [holding(FundType.TARGET_DATE, "Target 2050", 10_000, allocation=target_date_alloc)],
            ),
            account(
                "Taxable Brokerage", "Brokerage", TaxTreatment.TAXABLE,
                [
                    holding(FundType.US_STOCK, "VTI", 10_000),
                    holding(FundType.INTERNATIONAL_STOCK, "VXUS", 0),
                ],
            ),
        ]
        with pytest.raises(RebalanceError, match="hold at least"):
            compute_trades(accounts, target(70, 30, 0))


class TestBondsPreferTaxAdvantaged:
    def test_bonds_fill_tax_advantaged_before_taxable_when_room_exists(self):
        tax_adv_1 = account(
            "Roth IRA", "Roth1", TaxTreatment.TAX_ADVANTAGED,
            [holding(FundType.US_STOCK, "VTI", 2000), holding(FundType.US_BOND, "BND", 0)],
        )
        tax_adv_2 = account(
            "Traditional 401(k)", "401k", TaxTreatment.TAX_ADVANTAGED,
            [holding(FundType.US_STOCK, "VTI", 3000), holding(FundType.US_BOND, "BND", 0)],
        )
        taxable = account(
            "Taxable Brokerage", "Brokerage", TaxTreatment.TAXABLE,
            [holding(FundType.US_STOCK, "VTI", 5000), holding(FundType.US_BOND, "BND", 0)],
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
        us_stock_total = (
            tax_adv_1.total_value() + tax_adv_2.total_value() + taxable.total_value()
        )
        assert us_stock_total == Decimal(10_000)

    def test_bonds_overflow_to_taxable_when_tax_advantaged_insufficient(self):
        small_roth = account(
            "Roth IRA", "Roth", TaxTreatment.TAX_ADVANTAGED,
            [holding(FundType.US_STOCK, "VTI", 100), holding(FundType.US_BOND, "BND", 0)],
        )
        big_taxable = account(
            "Taxable Brokerage", "Brokerage", TaxTreatment.TAXABLE,
            [holding(FundType.US_STOCK, "VTI", 9900), holding(FundType.US_BOND, "BND", 0)],
        )
        # U.S. stock 60 / bond 40 on a $10,000 portfolio -> $4,000 bonds needed,
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


class TestInternationalPlacement:
    """Phase 3 prefers the international fund in taxable accounts, where its
    foreign withholding can be claimed as a credit. It ranks below the
    taxable-trading objective, so it is a tie-break and never a reason to
    trade."""

    TARGET_DATE_ALLOCATION = TargetDateAllocation(
        us_stock_pct=Decimal(60), international_stock_pct=Decimal(20), bond_pct=Decimal(20)
    )

    def test_international_is_bought_in_taxable_when_placement_costs_nothing(self):
        """Both accounts hold cash that has to be invested either way, so
        every placement costs the same amount of trading. A degenerate LP can
        satisfy this by luck -- see the test above it for the case that only
        phase 3 can produce -- but it pins the outcome against a future
        re-ranking that would leave the choice to the solver again."""
        accounts = [
            account("Taxable Brokerage", "Brokerage", TaxTreatment.TAXABLE, [
                holding(FundType.US_STOCK, "VTI", 0),
                holding(FundType.INTERNATIONAL_STOCK, "VXUS", 0),
                holding(FundType.CASH, "", 10_000),
            ]),
            account("Roth IRA", "Roth", TaxTreatment.TAX_ADVANTAGED, [
                holding(FundType.US_STOCK, "VTI", 0),
                holding(FundType.INTERNATIONAL_STOCK, "VXUS", 0),
                holding(FundType.CASH, "", 10_000),
            ]),
        ]
        trades = trades_by_key(compute_trades(accounts, target(50, 50, 0)))
        assert trades[("Brokerage", "VXUS")] == ("buy", Decimal("10000.00"))
        assert trades[("Roth", "VTI")] == ("buy", Decimal("10000.00"))
        assert ("Brokerage", "VTI") not in trades

    def test_international_is_moved_out_of_tax_advantaged_when_the_trades_are_free(self):
        """The discriminating case for phase 3. The Roth already holds all the
        international, and leaving it there needs one trade ($10,000 of VTI in
        taxable) against three for relocating it -- so the total-volume
        tie-break alone prefers leaving it. Phase 3 outranks that tie-break,
        and the two extra trades happen inside a Roth where they cost nothing,
        so the international moves to where its foreign tax is claimable."""
        accounts = [
            account("Taxable Brokerage", "Brokerage", TaxTreatment.TAXABLE, [
                holding(FundType.US_STOCK, "VTI", 0),
                holding(FundType.INTERNATIONAL_STOCK, "VXUS", 0),
                holding(FundType.CASH, "", 10_000),
            ]),
            account("Roth IRA", "Roth", TaxTreatment.TAX_ADVANTAGED, [
                holding(FundType.US_STOCK, "VTI", 0),
                holding(FundType.INTERNATIONAL_STOCK, "VXUS", 10_000),
            ]),
        ]
        trades = trades_by_key(compute_trades(accounts, target(50, 50, 0)))
        assert trades[("Brokerage", "VXUS")][0] == "buy"
        assert trades[("Roth", "VXUS")][0] == "sell"
        assert trades[("Roth", "VTI")][0] == "buy"
        assert ("Brokerage", "VTI") not in trades

    def test_never_sells_taxable_stock_to_move_international_in(self):
        """The same $10,000 of international is needed, but here buying it in
        taxable means selling appreciated stock there. Phase 2 outranks phase
        3, so the whole purchase happens in the Roth and taxable is untouched
        -- a couple of basis points of credit is not worth a realized gain."""
        accounts = [
            account("Taxable Brokerage", "Brokerage", TaxTreatment.TAXABLE, [
                holding(FundType.US_STOCK, "VTI", 10_000),
                holding(FundType.INTERNATIONAL_STOCK, "VXUS", 0),
            ]),
            account("Roth IRA", "Roth", TaxTreatment.TAX_ADVANTAGED, [
                holding(FundType.US_STOCK, "VTI", 10_000),
                holding(FundType.INTERNATIONAL_STOCK, "VXUS", 0),
            ]),
        ]
        result = compute_trades(accounts, target(50, 50, 0))
        assert not any(t.account_name == "Brokerage" for t in result.trades)
        trades = trades_by_key(result)
        assert trades[("Roth", "VXUS")] == ("buy", Decimal("10000.00"))

    def test_bonds_leaving_taxable_and_international_entering_it_cooperate(self):
        """The two placement objectives push the same way -- both displace
        U.S. stock from taxable -- so satisfying phase 1 fully still leaves
        phase 3 fully satisfied."""
        accounts = [
            account("Taxable Brokerage", "Brokerage", TaxTreatment.TAXABLE, [
                holding(FundType.US_STOCK, "VTI", 0),
                holding(FundType.INTERNATIONAL_STOCK, "VXUS", 0),
                holding(FundType.US_BOND, "BND", 0),
                holding(FundType.CASH, "", 10_000),
            ]),
            account("Roth IRA", "Roth", TaxTreatment.TAX_ADVANTAGED, [
                holding(FundType.US_STOCK, "VTI", 0),
                holding(FundType.INTERNATIONAL_STOCK, "VXUS", 0),
                holding(FundType.US_BOND, "BND", 0),
                holding(FundType.CASH, "", 10_000),
            ]),
        ]
        result = compute_trades(accounts, target(40, 30, 30))
        trades = trades_by_key(result)
        assert result.taxable_bond_dollars == Decimal(0)
        assert trades[("Brokerage", "VXUS")] == ("buy", Decimal("6000.00"))
        assert ("Brokerage", "BND") not in trades
        assert ("Roth", "VXUS") not in trades

    def test_a_target_date_fund_sleeve_is_out_of_phase_3s_reach(self):
        """The Roth's international exposure sits inside a target-date fund,
        and that account holds nothing else -- so the budget constraint pins
        it and phase 3 cannot relocate a cent of it, whatever it would prefer.
        The taxable account has an empty VXUS slot it could buy into, but the
        aggregate international target is already met, so nothing moves."""
        accounts = [
            account("Taxable Brokerage", "Brokerage", TaxTreatment.TAXABLE, [
                holding(FundType.US_STOCK, "VTI", 10_000),
                holding(FundType.INTERNATIONAL_STOCK, "VXUS", 0),
            ]),
            account("Roth IRA", "Roth", TaxTreatment.TAX_ADVANTAGED, [
                holding(FundType.TARGET_DATE, "Target 2050", 10_000,
                        allocation=self.TARGET_DATE_ALLOCATION),
            ]),
        ]
        result = compute_trades(accounts, target(80, 10, 10))
        assert result.trades == []


class TestTargetDateFunds:
    def test_target_date_fund_only_account_already_balanced(self):
        target_date_alloc = TargetDateAllocation(
            us_stock_pct=Decimal(60), international_stock_pct=Decimal(20), bond_pct=Decimal(20)
        )
        accounts = [
            account(
                "Roth 401(k)", "401k", TaxTreatment.TAX_ADVANTAGED,
                [holding(FundType.TARGET_DATE, "Target 2050", 10_000, allocation=target_date_alloc)],
            )
        ]
        result = compute_trades(accounts, target(60, 20, 20))
        assert result.trades == []

    def test_a_target_date_account_is_pinned_so_the_rest_absorbs_the_drift(self):
        """A target-date account has one slot, so its budget constraint fixes
        it outright. Its sleeves still count toward the aggregate targets --
        the individual-fund account has to work around them."""
        target_date_alloc = TargetDateAllocation(
            us_stock_pct=Decimal(60), international_stock_pct=Decimal(20), bond_pct=Decimal(20)
        )
        accounts = [
            account(
                "Roth 401(k)", "401k", TaxTreatment.TAX_ADVANTAGED,
                [holding(FundType.TARGET_DATE, "TargetFund", 5000, allocation=target_date_alloc)],
            ),
            account(
                "Taxable Brokerage", "Brokerage", TaxTreatment.TAXABLE,
                [
                    holding(FundType.US_STOCK, "VTI", 5000),
                    holding(FundType.INTERNATIONAL_STOCK, "VXUS", 0),
                ],
            ),
        ]
        # The fund contributes $3,000 US / $1,000 international / $1,000 bonds.
        # A 60/30/10 target on $10,000 wants $3,000 of international overall, so
        # the brokerage supplies the missing $2,000 and the fund is untouched.
        result = compute_trades(accounts, target(60, 30, 10))
        trades = trades_by_key(result)
        assert ("401k", "TargetFund") not in trades
        assert trades[("Brokerage", "VXUS")] == ("buy", Decimal("2000.00"))
        assert trades[("Brokerage", "VTI")] == ("sell", Decimal("2000.00"))

    def test_a_taxable_target_date_fund_is_never_liquidated_to_relocate_its_bonds(self):
        """Phase 1 wants no bonds in a taxable account, and $1,000 of this
        fund is bonds. Before accounts were one-kind-or-the-other it would
        sell the whole position to get at them -- realizing a gain on $5,000
        to relocate $1,000, in a portfolio already sitting on its target.
        Pinning the account makes that unreachable rather than merely
        undesirable."""
        target_date_alloc = TargetDateAllocation(
            us_stock_pct=Decimal(60), international_stock_pct=Decimal(20), bond_pct=Decimal(20)
        )
        accounts = [
            account(
                "Taxable Brokerage", "Brokerage", TaxTreatment.TAXABLE,
                [holding(FundType.TARGET_DATE, "Target 2050", 5000, allocation=target_date_alloc)],
            ),
            account(
                "Roth IRA", "Roth", TaxTreatment.TAX_ADVANTAGED,
                [
                    holding(FundType.US_STOCK, "VTI", 5000),
                    holding(FundType.US_BOND, "BND", 0),
                ],
            ),
        ]
        result = compute_trades(accounts, target(80, 10, 10))
        assert result.trades == []
        # The bonds are stuck in taxable, and the tool says so rather than trading.
        assert result.taxable_bond_dollars == Decimal(1000)
        assert result.warnings

    def test_target_date_fund_in_taxable_account_counts_toward_taxable_bonds(self):
        target_date_alloc = TargetDateAllocation(
            us_stock_pct=Decimal(60), international_stock_pct=Decimal(20), bond_pct=Decimal(20)
        )
        accounts = [
            account(
                "Taxable Brokerage", "Brokerage", TaxTreatment.TAXABLE,
                [holding(FundType.TARGET_DATE, "Target 2050", 10_000, allocation=target_date_alloc)],
            )
        ]
        result = compute_trades(accounts, target(60, 20, 20))
        # Only one slot exists in the whole portfolio, so nothing can move --
        # the $2,000 bond sleeve inside the taxable target-date fund is unavoidable.
        assert result.trades == []
        assert result.taxable_bond_dollars == Decimal("2000.00")
        assert len(result.warnings) == 1


class TestCashInvestment:
    def test_available_cash_gets_fully_invested(self):
        accounts = [
            account(
                "Taxable Brokerage", "Brokerage", TaxTreatment.TAXABLE,
                [
                    holding(FundType.US_STOCK, "VTI", 0),
                    holding(FundType.INTERNATIONAL_STOCK, "VXUS", 0),
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
    a cent off its real value, which yields an unfillable recommendation
    (e.g. 'buy $5,000.01' against exactly $5,000.00 of cash). Every trade set
    must leave each account's holdings summing to exactly its total."""

    def assert_conserved(self, accounts, result):
        deltas: dict[str, Decimal] = {}
        for t in result.trades:
            signed = t.amount if t.action == "buy" else -t.amount
            deltas[t.account_name] = deltas.get(t.account_name, Decimal(0)) + signed
        for account in accounts:
            net = deltas.get(account.name, Decimal(0))
            expected = account.available_cash()  # cash is spent, so buys exceed sells by that much
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
                    holding(FundType.US_STOCK, "VTI", 20_000),
                    holding(FundType.INTERNATIONAL_STOCK, "VXUS", 10_000),
                    holding(FundType.US_BOND, "BND", 0),
                ],
            ),
            account(
                "Traditional 401(k)", "Acme 401k", TaxTreatment.TAX_ADVANTAGED,
                [
                    holding(FundType.US_STOCK, "VTI", 40_000),
                    holding(FundType.US_BOND, "BND", 10_000),
                ],
            ),
            account(
                "Taxable Brokerage", "Fidelity Brokerage", TaxTreatment.TAXABLE,
                [
                    holding(FundType.US_STOCK, "VTI", 25_000),
                    holding(FundType.INTERNATIONAL_STOCK, "VXUS", 5_000),
                    holding(FundType.CASH, "", 5_000),
                ],
            ),
        ]
        result = compute_trades(
            accounts,
            TargetAllocation(
                us_stock_pct=Decimal("49.52"),
                international_stock_pct=Decimal("30.48"),
                bond_pct=Decimal(20),
            ),
        )
        self.assert_conserved(accounts, result)
        # The taxable account should spend exactly its cash, to the cent.
        taxable_buys = sum(
            t.amount for t in result.trades if t.account_name == "Fidelity Brokerage"
        )
        assert taxable_buys == Decimal("5000.00")

    def test_odd_cent_values_still_conserve(self):
        accounts = [
            account(
                "Roth IRA", "Roth", TaxTreatment.TAX_ADVANTAGED,
                [
                    holding(FundType.US_STOCK, "VTI", Decimal("3333.33")),
                    holding(FundType.INTERNATIONAL_STOCK, "VXUS", Decimal("3333.33")),
                    holding(FundType.US_BOND, "BND", Decimal("3333.34")),
                ],
            )
        ]
        result = compute_trades(
            accounts,
            TargetAllocation(
                us_stock_pct=Decimal("49.52"),
                international_stock_pct=Decimal("30.48"),
                bond_pct=Decimal(20),
            ),
        )
        self.assert_conserved(accounts, result)

    def test_thirds_across_uneven_accounts_conserve(self):
        # A 1/3-each target over accounts whose totals don't divide evenly is
        # the classic largest-remainder stress case.
        accounts = [
            account(
                "Roth IRA", f"Acct{i}", TaxTreatment.TAX_ADVANTAGED,
                [
                    holding(FundType.US_STOCK, "VTI", value),
                    holding(FundType.INTERNATIONAL_STOCK, "VXUS", 0),
                    holding(FundType.US_BOND, "BND", 0),
                ],
            )
            for i, value in enumerate([Decimal("1000.01"), Decimal("2000.02"), Decimal("3000.07")])
        ]
        result = compute_trades(
            accounts,
            TargetAllocation(
                us_stock_pct=Decimal("33.34"),
                international_stock_pct=Decimal("33.33"),
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
                    holding(FundType.US_STOCK, "VTI", Decimal("600.50")),
                    holding(FundType.INTERNATIONAL_STOCK, "VXUS", Decimal("399.50")),
                ],
            )
        ]
        result = compute_trades(accounts, target(60, 40, 0))
        assert result.trades == []
