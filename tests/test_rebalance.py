from decimal import Decimal

import pytest

from three_fund_rebalance.allocation import target_dollar_amounts, target_dollar_bounds
from three_fund_rebalance.models import (
    Account,
    FundType,
    Holding,
    TargetAllocation,
    TargetDateAllocation,
    TaxTreatment,
    to_cents,
)
from three_fund_rebalance.rebalance import (
    _TARGET_FUND_TYPES,
    RebalanceError,
    _resolve_allocation,
    compute_trades,
)


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
                TaxTreatment.TAX_DEFERRED,
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
                TaxTreatment.TAX_DEFERRED,
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
        accounts = [account("Roth IRA", "Roth", TaxTreatment.TAX_DEFERRED, [])]
        result = compute_trades(accounts, target(60, 20, 20))
        assert result.trades == []

    def test_empty_account_alongside_a_populated_one_is_skipped_cleanly(self):
        empty_account = account("Roth IRA", "EmptyRoth", TaxTreatment.TAX_DEFERRED, [])
        populated_account = account(
            "Traditional IRA", "TradIRA", TaxTreatment.TAX_DEFERRED,
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
                "Roth IRA", "Roth", TaxTreatment.TAX_DEFERRED,
                [holding(FundType.US_STOCK, "VTI", 100)],
            )
        ]
        with pytest.raises(RebalanceError, match="no arrangement of the funds you hold reaches your target"):
            compute_trades(accounts, target(100, 0, 0))


class TestValidation:
    def test_duplicate_account_names_rejected(self):
        accounts = [
            account(
                "Roth IRA", "Same", TaxTreatment.TAX_DEFERRED,
                [holding(FundType.US_STOCK, "VTI", 100)],
            ),
            account(
                "Traditional IRA", "Same", TaxTreatment.TAX_DEFERRED,
                [holding(FundType.US_STOCK, "VTI", 100)],
            ),
        ]
        with pytest.raises(RebalanceError, match="must be unique"):
            compute_trades(accounts, target(100, 0, 0))

    def test_cash_with_no_fund_holdings_rejected(self):
        accounts = [
            account(
                "Brokerage", "Brokerage", TaxTreatment.TAXABLE,
                [holding(FundType.CASH, "", 500)],
            )
        ]
        with pytest.raises(RebalanceError, match="no fund holdings declared"):
            compute_trades(accounts, target(100, 0, 0))

    def test_infeasible_bond_target_raises_clear_error(self):
        # No account declares a bond-capable slot anywhere.
        accounts = [
            account(
                "Brokerage", "Brokerage", TaxTreatment.TAXABLE,
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
                "Roth 401(k)", "401k", TaxTreatment.TAX_DEFERRED,
                [holding(FundType.TARGET_DATE, "Target 2050", 10_000, allocation=target_date_alloc)],
            ),
            account(
                "Brokerage", "Brokerage", TaxTreatment.TAXABLE,
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
            "Roth IRA", "Roth1", TaxTreatment.TAX_DEFERRED,
            [holding(FundType.US_STOCK, "VTI", 2000), holding(FundType.US_BOND, "BND", 0)],
        )
        tax_adv_2 = account(
            "Traditional 401(k)", "401k", TaxTreatment.TAX_DEFERRED,
            [holding(FundType.US_STOCK, "VTI", 3000), holding(FundType.US_BOND, "BND", 0)],
        )
        taxable = account(
            "Brokerage", "Brokerage", TaxTreatment.TAXABLE,
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
            "Roth IRA", "Roth", TaxTreatment.TAX_DEFERRED,
            [holding(FundType.US_STOCK, "VTI", 100), holding(FundType.US_BOND, "BND", 0)],
        )
        big_taxable = account(
            "Brokerage", "Brokerage", TaxTreatment.TAXABLE,
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
            account("Brokerage", "Brokerage", TaxTreatment.TAXABLE, [
                holding(FundType.US_STOCK, "VTI", 0),
                holding(FundType.INTERNATIONAL_STOCK, "VXUS", 0),
                holding(FundType.CASH, "", 10_000),
            ]),
            account("Roth IRA", "Roth", TaxTreatment.TAX_DEFERRED, [
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
            account("Brokerage", "Brokerage", TaxTreatment.TAXABLE, [
                holding(FundType.US_STOCK, "VTI", 0),
                holding(FundType.INTERNATIONAL_STOCK, "VXUS", 0),
                holding(FundType.CASH, "", 10_000),
            ]),
            account("Roth IRA", "Roth", TaxTreatment.TAX_DEFERRED, [
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
            account("Brokerage", "Brokerage", TaxTreatment.TAXABLE, [
                holding(FundType.US_STOCK, "VTI", 10_000),
                holding(FundType.INTERNATIONAL_STOCK, "VXUS", 0),
            ]),
            account("Roth IRA", "Roth", TaxTreatment.TAX_DEFERRED, [
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
            account("Brokerage", "Brokerage", TaxTreatment.TAXABLE, [
                holding(FundType.US_STOCK, "VTI", 0),
                holding(FundType.INTERNATIONAL_STOCK, "VXUS", 0),
                holding(FundType.US_BOND, "BND", 0),
                holding(FundType.CASH, "", 10_000),
            ]),
            account("Roth IRA", "Roth", TaxTreatment.TAX_DEFERRED, [
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
            account("Brokerage", "Brokerage", TaxTreatment.TAXABLE, [
                holding(FundType.US_STOCK, "VTI", 10_000),
                holding(FundType.INTERNATIONAL_STOCK, "VXUS", 0),
            ]),
            account("Roth IRA", "Roth", TaxTreatment.TAX_DEFERRED, [
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
                "Roth 401(k)", "401k", TaxTreatment.TAX_DEFERRED,
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
                "Roth 401(k)", "401k", TaxTreatment.TAX_DEFERRED,
                [holding(FundType.TARGET_DATE, "TargetFund", 5000, allocation=target_date_alloc)],
            ),
            account(
                "Brokerage", "Brokerage", TaxTreatment.TAXABLE,
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
                "Brokerage", "Brokerage", TaxTreatment.TAXABLE,
                [holding(FundType.TARGET_DATE, "Target 2050", 5000, allocation=target_date_alloc)],
            ),
            account(
                "Roth IRA", "Roth", TaxTreatment.TAX_DEFERRED,
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
                "Brokerage", "Brokerage", TaxTreatment.TAXABLE,
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
                "Brokerage", "Brokerage", TaxTreatment.TAXABLE,
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
                "Roth IRA", "Fidelity Roth", TaxTreatment.TAX_DEFERRED,
                [
                    holding(FundType.US_STOCK, "VTI", 20_000),
                    holding(FundType.INTERNATIONAL_STOCK, "VXUS", 10_000),
                    holding(FundType.US_BOND, "BND", 0),
                ],
            ),
            account(
                "Traditional 401(k)", "Acme 401k", TaxTreatment.TAX_DEFERRED,
                [
                    holding(FundType.US_STOCK, "VTI", 40_000),
                    holding(FundType.US_BOND, "BND", 10_000),
                ],
            ),
            account(
                "Brokerage", "Fidelity Brokerage", TaxTreatment.TAXABLE,
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
                "Roth IRA", "Roth", TaxTreatment.TAX_DEFERRED,
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
                "Roth IRA", f"Acct{i}", TaxTreatment.TAX_DEFERRED,
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
                "Roth IRA", "Roth", TaxTreatment.TAX_DEFERRED,
                [
                    holding(FundType.US_STOCK, "VTI", Decimal("600.50")),
                    holding(FundType.INTERNATIONAL_STOCK, "VXUS", Decimal("399.50")),
                ],
            )
        ]
        result = compute_trades(accounts, target(60, 40, 0))
        assert result.trades == []


class TestShelterTypeBondLocation:
    """Phase 4. Both shelters are exempt from tax today, so phase 1 is
    indifferent between them; what separates them is that a Roth or HSA never
    taxes the growth it shelters, which makes it the wrong place for the
    lowest-returning asset class."""

    def _roth_and_traditional(self):
        return [
            account("Roth IRA", "Roth", TaxTreatment.TAX_FREE, [
                holding(FundType.US_STOCK, "VTI", 50_000),
                holding(FundType.US_BOND, "BND", 0),
            ]),
            account("Traditional 401(k)", "401k", TaxTreatment.TAX_DEFERRED, [
                holding(FundType.US_STOCK, "VTI", 50_000),
                holding(FundType.US_BOND, "BND", 0),
            ]),
            account("Brokerage", "Brokerage", TaxTreatment.TAXABLE, [
                holding(FundType.US_STOCK, "VTI", 40_000),
                holding(FundType.INTERNATIONAL_STOCK, "VXUS", 60_000),
            ]),
        ]

    def test_bonds_fill_tax_deferred_space_before_tax_free_space(self):
        """Both shelters are the same size and hold the same funds, so this is
        a tie for every phase except this one -- and before phase 4 existed
        the solver resolved it by putting the whole bond sleeve in the Roth."""
        result = compute_trades(self._roth_and_traditional(), target(49.6, 30.4, 20))
        trades = trades_by_key(result)
        assert trades[("401k", "BND")] == ("buy", Decimal("40000.00"))
        assert ("Roth", "BND") not in trades
        assert ("Roth", "VTI") not in trades

    def test_bonds_overflow_into_tax_free_space_once_tax_deferred_is_full(self):
        """The preference is an ordering, not a prohibition: a bond target
        larger than the tax-deferred account still gets held, and the excess
        goes to the Roth rather than to taxable."""
        accounts = [
            account("Roth IRA", "Roth", TaxTreatment.TAX_FREE, [
                holding(FundType.US_STOCK, "VTI", 50_000),
                holding(FundType.US_BOND, "BND", 0),
            ]),
            account("Traditional 401(k)", "401k", TaxTreatment.TAX_DEFERRED, [
                holding(FundType.US_BOND, "BND", 20_000),
            ]),
            account("Brokerage", "Brokerage", TaxTreatment.TAXABLE, [
                holding(FundType.US_STOCK, "VTI", 30_000),
            ]),
        ]
        result = compute_trades(accounts, target(50, 0, 50))
        assert result.taxable_bond_dollars == Decimal(0)
        # $50,000 of bonds needed, only $20,000 of tax-deferred room.
        assert trades_by_key(result)[("Roth", "BND")] == ("buy", Decimal("30000.00"))

    def test_will_not_sell_in_taxable_to_move_bonds_between_shelters(self):
        """Phase 4 sits below the taxable-trading objective, so relocating
        bonds from a Roth to a 401(k) is worth doing only when it is free."""
        accounts = [
            account("Roth IRA", "Roth", TaxTreatment.TAX_FREE, [
                holding(FundType.US_BOND, "BND", 20_000),
            ]),
            account("Traditional 401(k)", "401k", TaxTreatment.TAX_DEFERRED, [
                holding(FundType.US_STOCK, "VTI", 20_000),
            ]),
            account("Brokerage", "Brokerage", TaxTreatment.TAXABLE, [
                holding(FundType.US_STOCK, "VTI", 60_000),
            ]),
        ]
        # Every account holds one fund, so its value is pinned: the only way
        # to empty the Roth of bonds would be to trade in taxable, and phase 2
        # outranks phase 4.
        result = compute_trades(accounts, target(80, 0, 20))
        assert result.trades == []


class TestWashSaleAvoidance:
    """Phase 3 and its warning. Selling at a loss and repurchasing the same
    security inside an IRA destroys the loss outright rather than deferring
    it, so the solver avoids the arrangement where it can and says so where
    it cannot."""

    def test_warns_when_a_taxable_sale_and_a_sheltered_purchase_cannot_be_separated(self):
        accounts = [
            account("Brokerage", "Brokerage", TaxTreatment.TAXABLE, [
                holding(FundType.US_STOCK, "VTI", 90_000),
                holding(FundType.INTERNATIONAL_STOCK, "VXUS", 10_000),
            ]),
            account("Roth IRA", "Roth", TaxTreatment.TAX_FREE, [
                holding(FundType.US_BOND, "BND", 40_000),
                holding(FundType.US_STOCK, "VTI", 10_000),
            ]),
        ]
        result = compute_trades(accounts, target(49.6, 30.4, 20))
        trades = trades_by_key(result)
        # The Roth holds only these two funds, so shedding bonds has nowhere
        # to go but VTI -- the very fund taxable is selling.
        assert trades[("Brokerage", "VTI")][0] == "sell"
        assert trades[("Roth", "VTI")][0] == "buy"
        warning = "\n".join(result.warnings)
        assert "VTI" in warning
        assert "wash sale" in warning
        assert "$10,000.00" in warning
        # The rule, its window and its standard. Without them "matched by
        # name" is a caveat about nothing in particular, and the reader has
        # no way to tell whether their own second fund is far enough away.
        assert "section 1091" in warning
        assert "substantially identical" in warning
        assert "within 30 days either side of the sale" in warning
        assert "in any account you control" in warning
        # Conditional and attributed, never "this is a wash sale".
        assert "this may be a wash sale" in warning
        assert "the IRS has taken the position (Rev. Rul. 2008-5)" in warning

    def test_no_warning_when_the_taxable_sale_is_of_a_different_fund(self):
        accounts = [
            account("Brokerage", "Brokerage", TaxTreatment.TAXABLE, [
                holding(FundType.US_STOCK, "VTI", 60_000),
                holding(FundType.INTERNATIONAL_STOCK, "VXUS", 40_000),
            ]),
            account("Traditional 401(k)", "401k", TaxTreatment.TAX_DEFERRED, [
                holding(FundType.US_STOCK, "FSKAX", 20_000),
                holding(FundType.US_BOND, "BND", 30_000),
            ]),
        ]
        result = compute_trades(accounts, target(49.6, 30.4, 20))
        assert result.warnings == []

    def test_selling_and_buying_the_same_fund_within_taxable_is_not_a_wash_warning(self):
        """Two taxable accounts are not the sheltered leg the rule is about --
        an ordinary wash sale between them at least preserves the basis."""
        accounts = [
            account("Brokerage", "One", TaxTreatment.TAXABLE, [
                holding(FundType.US_STOCK, "VTI", 50_000),
            ]),
            account("Brokerage", "Two", TaxTreatment.TAXABLE, [
                holding(FundType.US_STOCK, "VTI", 20_000),
                holding(FundType.US_BOND, "BND", 30_000),
            ]),
        ]
        result = compute_trades(accounts, target(70, 0, 30))
        assert all("wash sale" not in w for w in result.warnings)

    def test_fund_names_are_matched_ignoring_case_and_surrounding_space(self):
        accounts = [
            account("Brokerage", "Brokerage", TaxTreatment.TAXABLE, [
                holding(FundType.US_STOCK, " vti ", 90_000),
                holding(FundType.INTERNATIONAL_STOCK, "VXUS", 10_000),
            ]),
            account("Roth IRA", "Roth", TaxTreatment.TAX_FREE, [
                holding(FundType.US_BOND, "BND", 40_000),
                holding(FundType.US_STOCK, "VTI", 10_000),
            ]),
        ]
        result = compute_trades(accounts, target(49.6, 30.4, 20))
        assert any("wash sale" in w for w in result.warnings)

    def test_an_empty_taxable_slot_does_not_suppress_a_sheltered_purchase(self):
        """A taxable holding of zero cannot be sold, so it cannot pair into a
        wash sale -- and must not stand in the way of the international fund
        being moved out of tax-advantaged space, which is phase 5's job."""
        accounts = [
            account("Brokerage", "Brokerage", TaxTreatment.TAXABLE, [
                holding(FundType.US_STOCK, "VTI", 0),
                holding(FundType.INTERNATIONAL_STOCK, "VXUS", 0),
                holding(FundType.CASH, "", 10_000),
            ]),
            account("Roth IRA", "Roth", TaxTreatment.TAX_FREE, [
                holding(FundType.US_STOCK, "VTI", 0),
                holding(FundType.INTERNATIONAL_STOCK, "VXUS", 10_000),
            ]),
        ]
        trades = trades_by_key(compute_trades(accounts, target(50, 50, 0)))
        assert trades[("Brokerage", "VXUS")][0] == "buy"
        assert trades[("Roth", "VTI")][0] == "buy"


class TestRebalancingBand:
    """The band decides *whether* to rebalance. Once it says yes, the target
    is a point like any other."""

    def _drifted(self):
        # 51.67/31.67/16.67 against a 49.6/30.4/20 target: at most 3.3 points
        # out, which a 5-point band tolerates and an exact target does not.
        return [
            account("Brokerage", "Brokerage", TaxTreatment.TAXABLE, [
                holding(FundType.US_STOCK, "VTI", 58_000),
                holding(FundType.INTERNATIONAL_STOCK, "VXUS", 38_000),
            ]),
            account("Traditional 401(k)", "401k", TaxTreatment.TAX_DEFERRED, [
                holding(FundType.US_BOND, "BND", 20_000),
                holding(FundType.US_STOCK, "VTI", 4_000),
            ]),
        ]

    def test_drift_inside_the_band_is_left_alone(self):
        result = compute_trades(self._drifted(), target(49.6, 30.4, 20), Decimal(5))
        assert result.trades == []

    def test_the_same_drift_is_traded_away_without_a_band(self):
        """The band is what changes the answer here, not the portfolio."""
        result = compute_trades(self._drifted(), target(49.6, 30.4, 20), Decimal(0))
        assert trades_by_key(result)[("Brokerage", "VXUS")][0] == "sell"

    def test_a_band_does_not_stop_cash_being_invested(self):
        """An account's total is still an equality, so its cash is spent
        whatever the band says -- new money is the cheapest way to rebalance
        and the band must not suppress it."""
        accounts = [
            account("Brokerage", "Brokerage", TaxTreatment.TAXABLE, [
                holding(FundType.US_STOCK, "VTI", 50_000),
                holding(FundType.INTERNATIONAL_STOCK, "VXUS", 30_000),
                holding(FundType.CASH, "", 10_000),
            ]),
            account("Traditional 401(k)", "401k", TaxTreatment.TAX_DEFERRED, [
                holding(FundType.US_BOND, "BND", 20_000),
            ]),
        ]
        result = compute_trades(accounts, target(49.6, 30.4, 20), Decimal(5))
        bought = sum(t.amount for t in result.trades if t.action == "buy")
        sold = sum(t.amount for t in result.trades if t.action == "sell")
        assert bought - sold == Decimal("10000.00")

    def test_an_unreachable_target_stops_at_what_the_accounts_can_hold(self):
        """Not at the band edge. The 401(k) holds one fund, pinning U.S.
        stock at 60% against a 50% target, so bonds cannot reach 25% however
        the rest is arranged -- but they get to 15%, which is where the
        accounts run out, and nowhere near the 5% their 20-point band would
        have allowed."""
        accounts = [
            account("Traditional 401(k)", "401k", TaxTreatment.TAX_DEFERRED, [
                holding(FundType.US_STOCK, "VTI", 60_000),
            ]),
            account("Brokerage", "Brokerage", TaxTreatment.TAXABLE, [
                holding(FundType.INTERNATIONAL_STOCK, "VXUS", 38_000),
                holding(FundType.US_BOND, "BND", 2_000),  # below its 5% floor
            ]),
        ]
        result = compute_trades(accounts, target(50, 25, 25), Decimal(20))
        assert trades_by_key(result)[("Brokerage", "BND")] == ("buy", Decimal("13000.00"))

    def test_cash_alone_does_not_trigger_a_rebalance(self):
        """Cash is handled first and the band is then asked about what it
        leaves behind -- not about the portfolio still holding it. Every
        class here is inside its band, so the $100 goes to work in the
        laggard and nothing else moves. Asking the band about the cash
        instead meant a dividend swept up overnight rebalanced a portfolio
        that had nothing wrong with it, taxable sales included."""
        accounts = [
            account("Brokerage", "Brokerage", TaxTreatment.TAXABLE, [
                holding(FundType.US_STOCK, "VTI", 52_000),
                holding(FundType.INTERNATIONAL_STOCK, "VXUS", 29_000),
                holding(FundType.CASH, "", 100),
            ]),
            account("Traditional 401(k)", "401k", TaxTreatment.TAX_DEFERRED, [
                holding(FundType.US_BOND, "BND", 17_000),
                holding(FundType.US_STOCK, "VTI", 1_900),
            ]),
        ]
        result = compute_trades(accounts, target(50, 30, 20), Decimal(5))
        assert not [t for t in result.trades if t.account_name == "Brokerage" and t.action == "sell"]
        # The $100 reaches bonds, the class furthest below target, by way of
        # a free swap in the 401(k) -- the taxable account has no bond fund.
        assert trades_by_key(result) == {
            ("Brokerage", "VTI"): ("buy", Decimal("100.00")),
            ("401k", "VTI"): ("sell", Decimal("100.00")),
            ("401k", "BND"): ("buy", Decimal("100.00")),
        }

    def test_a_relative_band_catches_a_small_target_the_absolute_one_cannot(self):
        """5 points below a 5% bond target is zero bonds, so the absolute
        rule alone will watch a sleeve fall to a fifth of its target and call
        it fine. A quarter of the target puts the floor at 3.75%."""
        accounts = [
            account("Roth IRA", "Roth", TaxTreatment.TAX_FREE, [
                holding(FundType.US_STOCK, "FZROX", 58_800),
                holding(FundType.INTERNATIONAL_STOCK, "FZILX", 40_000),
                holding(FundType.US_BOND, "FXNAX", 1_200),
            ]),
        ]
        goal = target("58.8", "36.2", 5)
        assert compute_trades(accounts, goal, Decimal(5)).trades == []
        result = compute_trades(accounts, goal, Decimal(5), Decimal(25))
        assert trades_by_key(result)[("Roth", "FXNAX")] == ("buy", Decimal("3800.00"))

    def test_a_relative_band_does_not_tighten_a_large_target(self):
        """A quarter of a 58.8% target is 14.7 points, which would let the
        dominant class drift three times as far as the absolute rule allows.
        The tighter of the two binds, so nothing changes here."""
        accounts = [
            account("Roth IRA", "Roth", TaxTreatment.TAX_FREE, [
                holding(FundType.US_STOCK, "FZROX", 62_000),  # +3.2 points
                holding(FundType.INTERNATIONAL_STOCK, "FZILX", 33_000),
                holding(FundType.US_BOND, "FXNAX", 5_000),
            ]),
        ]
        goal = target("58.8", "36.2", 5)
        assert compute_trades(accounts, goal, Decimal(5), Decimal(25)).trades == []

    def test_a_band_can_make_an_otherwise_unreachable_target_reachable(self):
        """An account holding a single fund pins that fund's share of the
        portfolio: $60,000 of a $100,000 portfolio is 60% U.S. stock and
        cannot be less, so a 50% target is unreachable exactly -- but a
        10-point band reaches it."""
        accounts = [
            account("Traditional 401(k)", "401k", TaxTreatment.TAX_DEFERRED, [
                holding(FundType.US_STOCK, "VTI", 60_000),
            ]),
            account("Brokerage", "Brokerage", TaxTreatment.TAXABLE, [
                holding(FundType.INTERNATIONAL_STOCK, "VXUS", 20_000),
                holding(FundType.US_BOND, "BND", 20_000),
            ]),
        ]
        with pytest.raises(RebalanceError, match="cannot hold less"):
            compute_trades(accounts, target(50, 25, 25), Decimal(0))
        # No exception is the assertion: the band brings the floor into reach.
        compute_trades(accounts, target(50, 25, 25), Decimal(10))

    def test_the_band_edge_is_named_when_it_is_what_makes_a_target_unreachable(self):
        accounts = [
            account("Brokerage", "Brokerage", TaxTreatment.TAXABLE, [
                holding(FundType.US_STOCK, "VTI", 100_000),
            ]),
        ]
        with pytest.raises(RebalanceError, match="edge of your rebalancing band"):
            compute_trades(accounts, target(50, 0, 50), Decimal(10))


class TestCentResidualDistribution:
    def test_a_split_that_does_not_divide_into_cents_still_conserves_the_account(self):
        """Three near-equal targets against an odd total cannot all land on a
        whole cent, so rounding each slot independently leaves the account a
        cent off its own value. `_distribute_residual` places that cent where
        rounding moved furthest the other way.

        Worth keeping deliberately: the solver now lands on exact cents in
        most scenarios, so nothing else in this file exercises the path.
        """
        accounts = [
            account("Brokerage", "Brokerage", TaxTreatment.TAXABLE, [
                holding(FundType.US_STOCK, "VTI", "3333.33"),
                holding(FundType.INTERNATIONAL_STOCK, "VXUS", "3333.33"),
                holding(FundType.US_BOND, "BND", "3333.35"),
            ]),
        ]
        total_before = accounts[0].total_value()
        result = compute_trades(accounts, target("33.4", "33.3", "33.3"))

        bought = sum((t.amount for t in result.trades if t.action == "buy"), Decimal(0))
        sold = sum((t.amount for t in result.trades if t.action == "sell"), Decimal(0))
        assert bought == sold, "an account cannot spend more than it holds"
        assert total_before == Decimal("10000.01")
        for trade in result.trades:
            assert trade.amount == trade.amount.quantize(Decimal("0.01"))


class TestAllocationIsSettledBeforeLocation:
    """The regression this class exists for: the location phases are stated
    as "minimize this asset class in that kind of account", which only means
    "relocate it" while the class total is fixed. When the band left those
    totals as ranges, they could satisfy themselves by holding *less* of the
    asset class instead of moving it -- selling international rather than
    relocating it, and liquidating a bond fund to clear tax-free space in a
    portfolio that was already underweight bonds."""

    def _in_band_portfolio(self):
        # Every class inside a 5-point band of a 58.8/36.2/5.0 target, with
        # international parked in a Roth and a taxable account that has no
        # room to take it -- so phase 5 has no legal way to relocate, and
        # phase 4 has nowhere to move the Roth's bonds to.
        target_date = TargetDateAllocation(
            us_stock_pct=Decimal("64.1"),
            international_stock_pct=Decimal("34.34"),
            bond_pct=Decimal("1.56"),
        )
        return [
            account("Traditional 401(k)", "Trad 401k", TaxTreatment.TAX_DEFERRED, [
                holding(FundType.TARGET_DATE, "Target 2070", "45850.88", target_date),
            ]),
            account("Roth 401(k)", "Roth 401k", TaxTreatment.TAX_FREE, [
                holding(FundType.TARGET_DATE, "Target 2070", "10966.76", target_date),
            ]),
            account("Roth IRA", "Roth IRA", TaxTreatment.TAX_FREE, [
                holding(FundType.US_STOCK, "FZROX", "22549.52"),
                holding(FundType.INTERNATIONAL_STOCK, "FZILX", "15491.08"),
                holding(FundType.US_BOND, "FXNAX", "400.67"),
            ]),
            account("Brokerage", "Brokerage", TaxTreatment.TAXABLE, [
                holding(FundType.US_STOCK, "VTI", "6663.90"),
                holding(FundType.INTERNATIONAL_STOCK, "VXUS", "4895.78"),
            ]),
        ]

    def test_an_in_band_portfolio_is_left_alone_when_no_free_relocation_exists(self):
        accounts = self._in_band_portfolio()
        result = compute_trades(accounts, target("58.805", "36.195", 5), Decimal(5))
        assert result.trades == []

    def test_the_same_portfolio_still_rebalances_to_target_without_a_band(self):
        """The band is what changes the answer -- and when it does trade, it
        trades toward the bond target, not away from it."""
        accounts = self._in_band_portfolio()
        trades = trades_by_key(compute_trades(accounts, target("58.805", "36.195", 5), Decimal(0)))
        assert trades[("Roth IRA", "FXNAX")][0] == "buy"

    def test_a_location_phase_never_sells_an_asset_class_down_into_the_band(self):
        """Underweight bonds held in tax-free space: phase 4 would rather
        they were in a 401(k), but the only 401(k) here is a target-date fund
        that cannot be reached into. Preferring less bonds over relocated
        bonds is the failure mode."""
        accounts = self._in_band_portfolio()
        result = compute_trades(accounts, target("58.805", "36.195", 5), Decimal(5))
        sold = sum(
            (t.amount for t in result.trades if t.action == "sell" and t.fund_name == "FXNAX"),
            Decimal(0),
        )
        assert sold == Decimal(0)


class TestResolveAllocation:
    """The step that decides what each asset class should be worth, before
    anything decides where to hold it."""

    def _bounds(self, band):
        target_allocation = target(50, 30, 20)
        return target_dollar_amounts(target_allocation, Decimal(100_000)), target_dollar_bounds(
            target_allocation, Decimal(100_000), Decimal(band)
        )

    def _unconstrained_reach(self):
        return {fund_type: (0.0, 100_000.0) for fund_type in _TARGET_FUND_TYPES}

    def test_an_allocation_inside_the_band_is_left_exactly_where_it_is(self):
        targets, bounds = self._bounds(band=5)
        current = {
            "us_stock": Decimal(53_000),
            "international_stock": Decimal(28_000),
            "bond": Decimal(19_000),
        }
        resolved = _resolve_allocation(
            current, targets, bounds, self._unconstrained_reach(), Decimal(100_000)
        )
        assert resolved == current

    def test_an_allocation_outside_the_band_goes_all_the_way_back_to_target(self):
        """The band decides *whether* to rebalance, not how far. Stopping at
        the edge would leave the portfolio on the boundary, one small drift
        from tripping the band again."""
        targets, bounds = self._bounds(band=5)
        current = {
            "us_stock": Decimal(65_000),
            "international_stock": Decimal(25_000),
            "bond": Decimal(10_000),
        }
        resolved = _resolve_allocation(
            current, targets, bounds, self._unconstrained_reach(), Decimal(100_000)
        )
        assert {key: to_cents(value) for key, value in resolved.items()} == {
            "us_stock": Decimal("50000.00"),  # target, not the 55,000 ceiling
            "international_stock": Decimal("30000.00"),
            "bond": Decimal("20000.00"),
        }

    def test_one_class_out_of_band_rebalances_all_three(self):
        """The trigger is per class; the correction is not. U.S. stock is the
        only class outside its band here, and international -- comfortably
        inside its own -- is returned to target along with it."""
        targets, bounds = self._bounds(band=5)
        current = {
            "us_stock": Decimal(56_000),  # ceiling is 55,000
            "international_stock": Decimal(29_000),  # inside
            "bond": Decimal(15_000),  # exactly on its floor, so inside
        }
        resolved = _resolve_allocation(
            current, targets, bounds, self._unconstrained_reach(), Decimal(100_000)
        )
        assert to_cents(resolved["international_stock"]) == Decimal("30000.00")
        assert to_cents(resolved["bond"]) == Decimal("20000.00")

    def test_cash_that_brings_every_class_into_band_is_invested_without_a_sale(self):
        """Cash is spent before anything is sold, and it is enough on its own
        here: bonds start below their floor, and the $15,000 lifts all three
        classes to target without selling a dollar of anything."""
        targets, bounds = self._bounds(band=5)
        current = {
            "us_stock": Decimal(45_000),
            "international_stock": Decimal(27_000),
            "bond": Decimal(13_000),  # floor is 15,000
        }
        resolved = _resolve_allocation(
            current, targets, bounds, self._unconstrained_reach(), Decimal(100_000)
        )
        assert all(resolved[key] >= current[key] for key in current)
        assert to_cents(resolved["bond"]) == Decimal("20000.00")

    def test_cash_that_cannot_settle_the_band_falls_through_to_a_rebalance(self):
        """Cash only ever adds to a class, so it cannot pull one back under
        its ceiling. U.S. stock is 20 points over here and the $5,000 is no
        help, so the whole portfolio goes back to target as it would with no
        cash at all."""
        targets, bounds = self._bounds(band=5)
        current = {
            "us_stock": Decimal(70_000),  # ceiling is 55,000
            "international_stock": Decimal(20_000),
            "bond": Decimal(5_000),
        }  # $5,000 short of the $100,000 total: that is the cash
        resolved = _resolve_allocation(
            current, targets, bounds, self._unconstrained_reach(), Decimal(100_000)
        )
        assert {key: to_cents(value) for key, value in resolved.items()} == {
            "us_stock": Decimal("50000.00"),
            "international_stock": Decimal("30000.00"),
            "bond": Decimal("20000.00"),
        }

    def test_an_unreachable_target_settles_nearest_to_where_the_portfolio_sits(self):
        """An account holding a single fund pins that fund's share of the
        portfolio, so the closest reachable points to target are a whole face
        rather than a vertex -- with U.S. stock stuck at 60%, every split of
        the remaining $40,000 that keeps both classes inside their bands is
        exactly as far from target as every other. Moving least breaks the
        tie; without that the split is whichever vertex HiGHS returns."""
        targets, bounds = self._bounds(band=10)
        reach = dict(self._unconstrained_reach())
        reach[FundType.US_STOCK] = (60_000.0, 60_000.0)
        current = {
            "us_stock": Decimal(60_000),
            "international_stock": Decimal(38_000),
            "bond": Decimal(2_000),  # below its floor of 10,000
        }
        resolved = _resolve_allocation(current, targets, bounds, reach, Decimal(100_000))
        assert to_cents(resolved["us_stock"]) == Decimal("60000.00")
        # 30,000/10,000 moves $16,000; every other tied split moves more.
        assert to_cents(resolved["international_stock"]) == Decimal("30000.00")
        assert to_cents(resolved["bond"]) == Decimal("10000.00")

    def test_cash_is_steered_at_whatever_is_furthest_below_target(self):
        """Investing new money is the one way of rebalancing that costs
        nothing, so it goes where it does the most good."""
        targets, bounds = self._bounds(band=5)
        # $90,000 invested plus $10,000 of cash; bonds are the laggard.
        current = {
            "us_stock": Decimal(50_000),
            "international_stock": Decimal(30_000),
            "bond": Decimal(10_000),
        }
        resolved = _resolve_allocation(
            current, targets, bounds, self._unconstrained_reach(), Decimal(100_000)
        )
        assert sum(resolved.values()) == Decimal(100_000)
        # The whole $10,000 goes to bonds -- which is enough to reach the
        # bond target outright, so it stops there rather than at the band
        # edge, and nothing else has to be sold to get there.
        assert resolved["bond"] == Decimal(20_000)
        assert resolved["us_stock"] == Decimal(50_000)
        assert resolved["international_stock"] == Decimal(30_000)

    def test_the_resolved_total_never_exceeds_what_the_accounts_can_hold(self):
        targets, bounds = self._bounds(band=5)
        current = {
            "us_stock": Decimal(60_000),
            "international_stock": Decimal(30_000),
            "bond": Decimal(10_000),
        }
        reach = dict(self._unconstrained_reach())
        reach[FundType.US_BOND] = (0.0, 12_000.0)  # nothing can hold more bonds than this
        resolved = _resolve_allocation(current, targets, bounds, reach, Decimal(100_000))
        assert resolved["bond"] <= Decimal(12_000)
