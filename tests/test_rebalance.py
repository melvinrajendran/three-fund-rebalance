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
    return Account(
        account_type=account_type, name=name, tax_treatment=tax_treatment, holdings=holdings
    )


def target(us_stock, international, bond):
    return TargetAllocation(
        us_stock_pct=Decimal(us_stock),
        international_stock_pct=Decimal(international),
        bond_pct=Decimal(bond),
    )


def note_texts(result):
    """A note's label, finding and explanation as one string, so a test can
    assert on wording without caring which of the three parts carries it."""
    return [
        " ".join(part for part in (n.label, n.summary, n.detail) if part) for n in result.notes
    ]


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
        assert result.notes == []
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
        assert result.notes == []

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
        with pytest.raises(
            RebalanceError, match="no arrangement of the funds held reaches the target"
        ):
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


class TestUnreachableTargets:
    """A target the funds cannot reach is approximated and disclosed, never
    refused: the portfolio goes as close as the accounts allow and the
    warning says what stopped it. Only a target-date fund, or a partial set
    of slots like the ones here, can pin a class -- an account holding
    individual funds declares all three.
    """

    def test_a_class_no_account_can_hold_is_warned_about_not_rejected(self):
        # No account declares a bond-capable slot anywhere, so half this
        # portfolio's target has nowhere to go.
        accounts = [
            account(
                "Brokerage", "Brokerage", TaxTreatment.TAXABLE,
                [holding(FundType.US_STOCK, "VTI", 10_000)],
            )
        ]
        result = compute_trades(accounts, target(50, 0, 50))
        assert result.trades == [], "there is nothing to trade it into"
        assert any(
            "Bond target out of reach" in note
            and "No combination of the funds held reaches more than $0.00, or 0% of the "
            "portfolio" in note
            for note in note_texts(result)
        ), note_texts(result)

    def test_a_class_the_accounts_cannot_hold_less_of_is_warned_about(self):
        """A pinned account sets a floor as well as a ceiling: this fund is
        20% bonds and the account holds nothing else, so the portfolio cannot
        hold less than $2,000 of bonds however the rest is arranged."""
        target_date_alloc = TargetDateAllocation(
            us_stock_pct=Decimal(60), international_stock_pct=Decimal(20), bond_pct=Decimal(20)
        )
        accounts = [
            account(
                "Roth 401(k)", "401k", TaxTreatment.TAX_DEFERRED,
                [
                    holding(
                        FundType.TARGET_DATE, "Target 2050", 10_000, allocation=target_date_alloc
                    )
                ],
            ),
            account(
                "Brokerage", "Brokerage", TaxTreatment.TAXABLE,
                [
                    holding(FundType.US_STOCK, "VTI", 10_000),
                    holding(FundType.INTERNATIONAL_STOCK, "VXUS", 0),
                ],
            ),
        ]
        result = compute_trades(accounts, target(70, 30, 0))
        assert any(
            "Bond target out of reach" in note
            and "These accounts cannot hold less than $2,000.00, or 10% of the portfolio"
            in note
            for note in note_texts(result)
        ), note_texts(result)

    def test_the_rest_of_the_portfolio_is_still_rebalanced_around_it(self):
        """The point of approximating rather than refusing: the two classes
        that *can* be reached still are. The target-date account pins $2,000
        of bonds against a 0% bond target, and U.S. stock still lands exactly
        on its $10,000 target -- international absorbs the whole shortfall,
        because it is the only class with anywhere left to give."""
        target_date_alloc = TargetDateAllocation(
            us_stock_pct=Decimal(60), international_stock_pct=Decimal(20), bond_pct=Decimal(20)
        )
        accounts = [
            account(
                "Roth 401(k)", "401k", TaxTreatment.TAX_DEFERRED,
                [
                    holding(
                        FundType.TARGET_DATE, "Target 2050", 10_000, allocation=target_date_alloc
                    )
                ],
            ),
            account(
                "Brokerage", "Brokerage", TaxTreatment.TAXABLE,
                [
                    holding(FundType.US_STOCK, "VTI", 10_000),
                    holding(FundType.INTERNATIONAL_STOCK, "VXUS", 0),
                ],
            ),
        ]
        trades = trades_by_key(compute_trades(accounts, target(50, 50, 0)))
        assert trades[("Brokerage", "VTI")] == ("sell", Decimal("6000.00"))
        assert trades[("Brokerage", "VXUS")] == ("buy", Decimal("6000.00"))

    def test_a_target_sitting_exactly_on_a_reachable_edge_is_not_reported(self):
        """`_asset_class_reach` works in floats, so a class pinned exactly on
        its target -- this bond target is the target-date fund's own sleeve,
        with no band at all -- can miss the edge by a billionth of a dollar.
        Reporting that as a target out of reach would be reporting solver
        noise."""
        target_date_alloc = TargetDateAllocation(
            us_stock_pct=Decimal("34.98"),
            international_stock_pct=Decimal("32.73"),
            bond_pct=Decimal("32.29"),
        )
        accounts = [
            account("Traditional 401(k)", "401k", TaxTreatment.TAX_DEFERRED, [
                holding(
                    FundType.TARGET_DATE, "Target 2065", "958489.36", allocation=target_date_alloc
                ),
            ]),
        ]
        # The figures are chosen: the float floor lands 6e-11 above the
        # Decimal target, which is exactly the kind of gap _capacity_warnings
        # must not read as a target out of reach.
        result = compute_trades(accounts, target("34.98", "32.73", "32.29"))
        assert result.trades == []
        assert result.notes == []

    def test_a_target_the_accounts_can_reach_warns_about_nothing(self):
        accounts = [
            account(
                "Roth IRA", "Roth", TaxTreatment.TAX_FREE,
                [
                    holding(FundType.US_STOCK, "VTI", 10_000),
                    holding(FundType.US_BOND, "BND", 0),
                ],
            )
        ]
        result = compute_trades(accounts, target(80, 0, 20))
        assert result.notes == []


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
        assert result.notes == []
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
        assert len(result.notes) == 1
        assert "3,900.00" in note_texts(result)[0] or "3900" in note_texts(result)[0]

        roth_bond = next(
            t for t in result.trades if t.account_name == "Roth" and t.fund_name == "BND"
        )
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


class TestInternationalLocationDisclosure:
    """Phase 5 wants international in taxable; phase 2 outranks it. When that
    ranking sends international the other way the plan looks contrary to what
    it optimizes for, so the note says which preference gave way."""

    TARGET_DATE_ALLOCATION = TargetDateAllocation(
        us_stock_pct=Decimal(60), international_stock_pct=Decimal(20), bond_pct=Decimal(20)
    )

    def _complaint_scenario(self):
        """Taxable is all U.S. stock with gains behind it, so the whole
        international shortfall has to be made up inside the shelters."""
        return [
            account("Brokerage", "Brokerage", TaxTreatment.TAXABLE, [
                holding(FundType.US_STOCK, "VTI", 150_000),
                holding(FundType.INTERNATIONAL_STOCK, "VXUS", 40_000),
                holding(FundType.US_BOND, "BND", 0),
                holding(FundType.CASH, "", 2_500),
            ]),
            account("Roth IRA", "Roth", TaxTreatment.TAX_FREE, [
                holding(FundType.US_STOCK, "VTI", 60_000),
                holding(FundType.INTERNATIONAL_STOCK, "VXUS", 0),
                holding(FundType.US_BOND, "BND", 0),
            ]),
            account("Traditional 401(k)", "401k", TaxTreatment.TAX_DEFERRED, [
                holding(FundType.US_STOCK, "VTI", 80_000),
                holding(FundType.INTERNATIONAL_STOCK, "VXUS", 0),
                holding(FundType.US_BOND, "BND", 20_000),
            ]),
        ]

    def test_international_bought_in_a_shelter_is_disclosed(self):
        result = compute_trades(
            self._complaint_scenario(), target("49.6", "30.4", 20), Decimal(5), Decimal(25)
        )
        note = "\n".join(note_texts(result))
        assert "International in tax-advantaged" in note
        # Both sheltered purchases, not just the larger one.
        assert "$64,660.00" in note
        assert "gives up a foreign tax credit" in note
        # The reason it happened anyway, in the reader's terms rather than the
        # solver's -- without it the order reads as a bug, and "ranks higher"
        # names a phase ordering the reader has never seen.
        assert "would have meant selling something there" in note
        assert "ranks higher" not in note

    def test_nothing_is_said_when_no_account_is_taxable(self):
        """There is no alternative placement to describe, so the note would
        report a preference that was never available."""
        accounts = [
            account("Roth IRA", "Roth", TaxTreatment.TAX_FREE, [
                holding(FundType.US_STOCK, "VTI", 90_000),
                holding(FundType.INTERNATIONAL_STOCK, "VXUS", 0),
                holding(FundType.US_BOND, "BND", 10_000),
            ]),
            account("Traditional 401(k)", "401k", TaxTreatment.TAX_DEFERRED, [
                holding(FundType.US_STOCK, "VTI", 80_000),
                holding(FundType.INTERNATIONAL_STOCK, "VXUS", 0),
                holding(FundType.US_BOND, "BND", 20_000),
            ]),
        ]
        result = compute_trades(accounts, target("49.6", "30.4", 20))
        assert any(t.action == "buy" and t.fund_name == "VXUS" for t in result.trades)
        assert not any("International in tax-advantaged" in n for n in note_texts(result))

    def test_a_target_date_funds_international_sleeve_is_not_a_disclosed_purchase(self):
        """The same call phase 5 makes: a target-date fund is not
        majority-foreign, so buying one passes no credit through from either
        kind of account and there is nothing here to have given up."""
        accounts = [
            account("Traditional 401(k)", "401k", TaxTreatment.TAX_DEFERRED, [
                holding(FundType.TARGET_DATE, "Target 2050", 100_000,
                        allocation=self.TARGET_DATE_ALLOCATION),
            ]),
            account("Brokerage", "Brokerage", TaxTreatment.TAXABLE, [
                holding(FundType.US_STOCK, "VTI", 60_000),
                holding(FundType.INTERNATIONAL_STOCK, "VXUS", 40_000),
                holding(FundType.US_BOND, "BND", 0),
            ]),
        ]
        result = compute_trades(accounts, target("49.6", "30.4", 20))
        assert not any("International in tax-advantaged" in n for n in note_texts(result))


class TestTargetDateFunds:
    def test_target_date_fund_only_account_already_balanced(self):
        target_date_alloc = TargetDateAllocation(
            us_stock_pct=Decimal(60), international_stock_pct=Decimal(20), bond_pct=Decimal(20)
        )
        accounts = [
            account(
                "Roth 401(k)", "401k", TaxTreatment.TAX_DEFERRED,
                [
                    holding(
                        FundType.TARGET_DATE, "Target 2050", 10_000, allocation=target_date_alloc
                    )
                ],
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
        assert result.notes

    def test_target_date_fund_in_taxable_account_counts_toward_taxable_bonds(self):
        target_date_alloc = TargetDateAllocation(
            us_stock_pct=Decimal(60), international_stock_pct=Decimal(20), bond_pct=Decimal(20)
        )
        accounts = [
            account(
                "Brokerage", "Brokerage", TaxTreatment.TAXABLE,
                [
                    holding(
                        FundType.TARGET_DATE, "Target 2050", 10_000, allocation=target_date_alloc
                    )
                ],
            )
        ]
        result = compute_trades(accounts, target(60, 20, 20))
        # Only one slot exists in the whole portfolio, so nothing can move --
        # the $2,000 bond sleeve inside the taxable target-date fund is unavoidable.
        assert result.trades == []
        assert result.taxable_bond_dollars == Decimal("2000.00")
        assert len(result.notes) == 1


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
                "Roth IRA", "Vanguard Roth", TaxTreatment.TAX_DEFERRED,
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
                "Brokerage", "Vanguard Brokerage", TaxTreatment.TAXABLE,
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
            t.amount for t in result.trades if t.account_name == "Vanguard Brokerage"
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
        warning = "\n".join(note_texts(result))
        assert "VTI" in warning
        assert "wash sale" in warning
        assert "$10,000.00" in warning
        # Conditional, never "this is a wash sale": the tool cannot see cost
        # basis, trade dates, or purchases made anywhere else in the window.
        assert "this may be a wash sale" in warning
        # The note states the finding and stops. Reciting section 1091's
        # window and standard, and the IRS's position on a replacement bought
        # inside an IRA, ran to seven lines -- the largest block below the
        # orders, and statute rather than anything about this portfolio.
        for statute in ("section 1091", "substantially identical", "30 days", "Rev. Rul."):
            assert statute not in warning, statute

    def test_no_warning_when_the_taxable_sale_is_of_a_different_fund(self):
        accounts = [
            account("Brokerage", "Brokerage", TaxTreatment.TAXABLE, [
                holding(FundType.US_STOCK, "VTI", 60_000),
                holding(FundType.INTERNATIONAL_STOCK, "VXUS", 40_000),
            ]),
            account("Traditional 401(k)", "401k", TaxTreatment.TAX_DEFERRED, [
                holding(FundType.US_STOCK, "VFIAX", 20_000),
                holding(FundType.US_BOND, "BND", 30_000),
            ]),
        ]
        result = compute_trades(accounts, target(49.6, 30.4, 20))
        assert result.notes == []

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
        assert all("wash sale" not in w for w in note_texts(result))

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
        assert any("wash sale" in w for w in note_texts(result))

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


class TestDeclaredCapacity:
    """A slot exists because the account *can* hold that asset class, not
    because it currently does. This is what the prompts collect now: an
    account holding individual funds declares all three, whatever it holds."""

    def _roth(self, holdings):
        return [account("Roth IRA", "Roth", TaxTreatment.TAX_FREE, holdings)]

    def test_a_fund_declared_with_no_position_is_bought_into(self):
        accounts = self._roth([
            holding(FundType.US_STOCK, "VTI", 10_000),
            holding(FundType.US_BOND, "BND", 0),
        ])
        trades = trades_by_key(compute_trades(accounts, target(80, 0, 20)))
        assert trades[("Roth", "VTI")] == ("sell", Decimal("2000.00"))
        assert trades[("Roth", "BND")] == ("buy", Decimal("2000.00"))

    def test_without_that_slot_the_same_target_is_out_of_reach(self):
        """The pair that earns its keep: drop the empty declaration and the
        bond target has nowhere to go, so the portfolio stays 100% stock and
        is told why. Answering "no" to a fund not yet bought used to do
        exactly this."""
        accounts = self._roth([holding(FundType.US_STOCK, "VTI", 10_000)])
        result = compute_trades(accounts, target(80, 0, 20))
        assert result.trades == []
        assert any(
            "Bond target out of reach" in note for note in note_texts(result)
        ), note_texts(result)


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
        assert not [
            t for t in result.trades if t.account_name == "Brokerage" and t.action == "sell"
        ]
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
                holding(FundType.US_STOCK, "VTSAX", 58_800),
                holding(FundType.INTERNATIONAL_STOCK, "VTIAX", 40_000),
                holding(FundType.US_BOND, "VBTLX", 1_200),
            ]),
        ]
        goal = target("58.8", "36.2", 5)
        assert compute_trades(accounts, goal, Decimal(5)).trades == []
        result = compute_trades(accounts, goal, Decimal(5), Decimal(25))
        assert trades_by_key(result)[("Roth", "VBTLX")] == ("buy", Decimal("3800.00"))

    def test_a_relative_band_does_not_tighten_a_large_target(self):
        """A quarter of a 58.8% target is 14.7 points, which would let the
        dominant class drift three times as far as the absolute rule allows.
        The tighter of the two binds, so nothing changes here."""
        accounts = [
            account("Roth IRA", "Roth", TaxTreatment.TAX_FREE, [
                holding(FundType.US_STOCK, "VTSAX", 62_000),  # +3.2 points
                holding(FundType.INTERNATIONAL_STOCK, "VTIAX", 33_000),
                holding(FundType.US_BOND, "VBTLX", 5_000),
            ]),
        ]
        goal = target("58.8", "36.2", 5)
        assert compute_trades(accounts, goal, Decimal(5), Decimal(25)).trades == []

    def test_the_band_does_not_decide_whether_a_target_can_be_solved(self):
        """An account holding a single fund pins that fund's share of the
        portfolio: $60,000 of a $100,000 portfolio is 60% U.S. stock and
        cannot be less, so a 50% target is unreachable exactly. The band has
        no say in that. It used to: the same portfolio was an error at a band
        of 0 and a plan at a band of 10, which made widening a band a way to
        talk the solver round rather than a statement of policy."""
        accounts = [
            account("Traditional 401(k)", "401k", TaxTreatment.TAX_DEFERRED, [
                holding(FundType.US_STOCK, "VTI", 60_000),
            ]),
            account("Brokerage", "Brokerage", TaxTreatment.TAXABLE, [
                holding(FundType.INTERNATIONAL_STOCK, "VXUS", 20_000),
                holding(FundType.US_BOND, "BND", 20_000),
            ]),
        ]
        exact = compute_trades(accounts, target(50, 25, 25), Decimal(0))
        banded = compute_trades(accounts, target(50, 25, 25), Decimal(10))
        assert exact.trades == banded.trades == []
        notes = note_texts(exact)
        assert any("U.S. stock target" in warning for warning in notes), notes

    def test_a_class_pinned_outside_its_band_stops_re_triggering_once_settled(self):
        """A band that can never be satisfied is a band that never says
        "leave it alone", which would drive the whole portfolio back to exact
        target on every run and trade on any drift at all. The trigger reads
        the band widened to what the accounts can hold, so the run that gets
        a pinned class as close as it can go is the last run that trades."""
        target_date_alloc = TargetDateAllocation(
            us_stock_pct=Decimal(80), international_stock_pct=Decimal(0), bond_pct=Decimal(20)
        )
        # The 401(k) pins $2,000 of bonds against a 0% bond target; the Roth
        # holds $1,000 more, and those are the only ones that can be sold.
        accounts = [
            account("Traditional 401(k)", "401k", TaxTreatment.TAX_DEFERRED, [
                holding(FundType.TARGET_DATE, "Target 2050", 10_000, allocation=target_date_alloc),
            ]),
            account("Roth IRA", "Roth", TaxTreatment.TAX_FREE, [
                holding(FundType.US_STOCK, "VTI", 9_000),
                holding(FundType.INTERNATIONAL_STOCK, "VXUS", 0),
                holding(FundType.US_BOND, "BND", 1_000),
            ]),
        ]
        first = trades_by_key(compute_trades(accounts, target(60, 40, 0), Decimal(5), Decimal(25)))
        assert first[("Roth", "BND")] == ("sell", Decimal("1000.00"))
        assert first[("Roth", "VTI")] == ("sell", Decimal("5000.00"))
        assert first[("Roth", "VXUS")] == ("buy", Decimal("6000.00"))

        # Exactly where those orders leave it.
        settled = [
            accounts[0],
            account("Roth IRA", "Roth", TaxTreatment.TAX_FREE, [
                holding(FundType.US_STOCK, "VTI", 4_000),
                holding(FundType.INTERNATIONAL_STOCK, "VXUS", 6_000),
                holding(FundType.US_BOND, "BND", 0),
            ]),
        ]
        assert compute_trades(settled, target(60, 40, 0), Decimal(5), Decimal(25)).trades == []


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
            us_stock_pct=Decimal("63.2"),
            international_stock_pct=Decimal("34.9"),
            bond_pct=Decimal("1.9"),
        )
        return [
            account("Traditional 401(k)", "Trad 401k", TaxTreatment.TAX_DEFERRED, [
                holding(FundType.TARGET_DATE, "Target 2065", "48086.90", target_date),
            ]),
            account("Roth 401(k)", "Roth 401k", TaxTreatment.TAX_FREE, [
                holding(FundType.TARGET_DATE, "Target 2065", "16717.72", target_date),
            ]),
            account("Roth IRA", "Roth IRA", TaxTreatment.TAX_FREE, [
                holding(FundType.US_STOCK, "VTSAX", "19381.57"),
                holding(FundType.INTERNATIONAL_STOCK, "VTIAX", "11298.33"),
                holding(FundType.US_BOND, "VBTLX", "711.52"),
            ]),
            account("Brokerage", "Brokerage", TaxTreatment.TAXABLE, [
                holding(FundType.US_STOCK, "VTI", "5986.78"),
                holding(FundType.INTERNATIONAL_STOCK, "VXUS", "4682.90"),
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
        assert trades[("Roth IRA", "VBTLX")][0] == "buy"

    def test_a_location_phase_never_sells_an_asset_class_down_into_the_band(self):
        """Underweight bonds held in tax-free space: phase 4 would rather
        they were in a 401(k), but the only 401(k) here is a target-date fund
        that cannot be reached into. Preferring less bonds over relocated
        bonds is the failure mode."""
        accounts = self._in_band_portfolio()
        result = compute_trades(accounts, target("58.805", "36.195", 5), Decimal(5))
        sold = sum(
            (t.amount for t in result.trades if t.action == "sell" and t.fund_name == "VBTLX"),
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


class TestClassTotalsSumToThePortfolio:
    """The main LP states each account's budget and each asset class's total
    as equalities, and every slot's three class coefficients sum to 1 -- so
    adding the three class rows together reproduces the account rows. The two
    families are consistent only while the class totals sum to exactly the
    portfolio total, and a program that breaks that is not merely imprecise,
    it is infeasible. These are the ways it got broken.
    """

    TARGET_DATE_ALLOCATION = TargetDateAllocation(
        us_stock_pct=Decimal("63.2"),
        international_stock_pct=Decimal("34.9"),
        bond_pct=Decimal("1.9"),
    )

    def test_class_totals_that_do_not_round_to_the_portfolio_total_still_solve(self):
        """At a 58.805/36.195/5 target on $98,464.07 the three class totals
        round at six decimal places to a millionth of a dollar away from the
        portfolio -- far above the solver's feasibility tolerance, so it
        rejected the whole thing. The figures are chosen for that: an
        ordinary-looking portfolio, and about one in seven of them lands
        here."""
        accounts = [
            account(
                "Traditional 401(k)",
                "Employer 401(k)",
                TaxTreatment.TAX_DEFERRED,
                [
                    holding(
                        FundType.TARGET_DATE,
                        "Target Date 2065 Fund",
                        "31908.17",
                        self.TARGET_DATE_ALLOCATION,
                    )
                ],
            ),
            account(
                "Roth 401(k)",
                "Employer Roth 401(k)",
                TaxTreatment.TAX_FREE,
                [
                    holding(
                        FundType.TARGET_DATE,
                        "Target Date 2065 Fund",
                        "17288.58",
                        self.TARGET_DATE_ALLOCATION,
                    )
                ],
            ),
            account(
                "Roth IRA",
                "Roth IRA",
                TaxTreatment.TAX_FREE,
                [
                    holding(FundType.US_STOCK, "VTSAX", "19066.07"),
                    holding(FundType.INTERNATIONAL_STOCK, "VTIAX", "16871.98"),
                    holding(FundType.US_BOND, "VBTLX", "852.43"),
                ],
            ),
            account(
                "Brokerage",
                "Brokerage",
                TaxTreatment.TAXABLE,
                [
                    holding(FundType.US_STOCK, "VTI", "7840.20"),
                    holding(FundType.INTERNATIONAL_STOCK, "VXUS", "4636.46"),
                    holding(FundType.CASH, "Cash", "0.18"),
                ],
            ),
        ]
        result = compute_trades(
            accounts,
            target("58.805", "36.195", 5),
            band_pct=Decimal(5),
            relative_band_pct=Decimal(25),
        )
        # Bonds sit at 1.8% against a 5% target, so the band is tripped and
        # the correction is real -- and it happens entirely inside the Roth
        # IRA, the only account with room to move.
        assert trades_by_key(result) == {
            ("Roth IRA", "VTIAX"): ("sell", Decimal("3039.22")),
            ("Roth IRA", "VTSAX"): ("sell", Decimal("96.82")),
            ("Roth IRA", "VBTLX"): ("buy", Decimal("3136.04")),
        }

    @pytest.mark.parametrize(
        "us_stock, international, bond",
        [("64.0", "34.3", "1.6"), ("64.2", "34.4", "1.5")],  # sums to 99.9, then 100.1
    )
    def test_a_target_date_fund_that_does_not_sum_to_exactly_100_still_solves(
        self, us_stock, international, bond
    ):
        """A fact sheet rounds each sleeve to a tenth, so the three need not
        come to 100 -- TargetDateAllocation allows a tenth either way. Read
        as literal percentages, such a fund's sleeves contradict its own
        account budget by a tenth of a percent of the account, which is a
        thousand times the slack the solver has."""
        allocation = TargetDateAllocation(
            us_stock_pct=Decimal(us_stock),
            international_stock_pct=Decimal(international),
            bond_pct=Decimal(bond),
        )
        accounts = [
            account(
                "Traditional 401(k)",
                "401k",
                TaxTreatment.TAX_DEFERRED,
                [holding(FundType.TARGET_DATE, "TDF", "31908.17", allocation)],
            ),
            account(
                "Roth IRA",
                "Roth",
                TaxTreatment.TAX_FREE,
                [
                    holding(FundType.US_STOCK, "VTSAX", "19066.07"),
                    holding(FundType.INTERNATIONAL_STOCK, "VTIAX", "16871.98"),
                    holding(FundType.US_BOND, "VBTLX", "5852.43"),
                ],
            ),
        ]
        result = compute_trades(
            accounts,
            target("58.805", "36.195", 5),
            band_pct=Decimal(5),
            relative_band_pct=Decimal(25),
        )
        assert result.trades  # the portfolio is out of band; what matters is that it solved

    @pytest.mark.parametrize(
        "current",
        [
            # In band and fully invested; cash enough to settle the band;
            # cash that cannot; and a plain rebalance. One per exit path.
            {"us_stock": "58000.00", "international_stock": "35540.87", "bond": "4923.20"},
            {"us_stock": "57000.00", "international_stock": "35000.00", "bond": "4900.00"},
            {"us_stock": "70000.00", "international_stock": "25000.00", "bond": "3000.00"},
            {"us_stock": "75000.00", "international_stock": "18540.87", "bond": "4923.20"},
        ],
    )
    def test_the_resolved_class_totals_sum_to_the_portfolio_total_exactly(self, current):
        total_value = Decimal("98464.07")
        target_allocation = target("58.805", "36.195", 5)
        resolved = _resolve_allocation(
            {key: Decimal(value) for key, value in current.items()},
            target_dollar_amounts(target_allocation, total_value),
            target_dollar_bounds(target_allocation, total_value, Decimal(5), Decimal(25)),
            {fund_type: (0.0, float(total_value)) for fund_type in _TARGET_FUND_TYPES},
            total_value,
        )
        assert sum(resolved.values(), Decimal(0)) == total_value

    def test_a_multi_billion_dollar_portfolio_is_not_rejected_over_float_noise(self):
        """The redundant third class equality is satisfiable only if the
        coefficients sum to 1 to the last bit, which floating point does not
        do. A relative error of 1e-16 is nothing until the portfolio is large
        enough that it exceeds the solver's *absolute* feasibility tolerance
        -- around $8B against HiGHS's 1e-7 -- at which point a perfectly
        ordinary portfolio is rejected outright. This one failed before the
        third equality was left implicit, target-date percentages summing to
        exactly 100 included."""
        accounts = [
            account(
                "Traditional 401(k)",
                "401k",
                TaxTreatment.TAX_DEFERRED,
                [
                    holding(
                        FundType.TARGET_DATE,
                        "TDF",
                        "4695425564",
                        self.TARGET_DATE_ALLOCATION,
                    )
                ],
            ),
            account(
                "Roth IRA",
                "Roth",
                TaxTreatment.TAX_FREE,
                [
                    holding(FundType.US_STOCK, "VTSAX", "1961973069"),
                    holding(FundType.INTERNATIONAL_STOCK, "VTIAX", "1723938499"),
                    holding(FundType.US_BOND, "VBTLX", "649467786"),
                ],
            ),
        ]
        result = compute_trades(
            accounts,
            target("58.805", "36.195", 5),
            band_pct=Decimal(5),
            relative_band_pct=Decimal(25),
        )
        assert result.trades
