from dataclasses import replace
from decimal import Decimal

from three_fund_rebalance.config import MAX_ACCOUNT_NAME_LENGTH
from three_fund_rebalance.formatting import (
    INDENT_UNIT,
    PROSE_MAX_WIDTH,
    format_account_heading,
    format_percent,
    prose_width,
    table_width,
)
from three_fund_rebalance.models import (
    Account,
    FundType,
    Holding,
    TargetAllocation,
    TargetDateAllocation,
    TaxTreatment,
    Trade,
)
from three_fund_rebalance.rebalance import RebalanceResult
from three_fund_rebalance.report import (
    RebalanceInputs,
    allocation_after_trades,
    describe_account_trades,
    format_report,
    group_trades_by_account,
    summarize_allocation,
)
from three_fund_rebalance.vt_allocation import VTAllocationResult


def inputs(accounts, target, band_pct="0"):
    """The report recaps what it was asked, so it needs the whole set of
    answers -- not just the accounts and the target it computes against."""
    return RebalanceInputs(
        stock_pct=target.us_stock_pct + target.international_stock_pct,
        bond_pct=target.bond_pct,
        vt=VTAllocationResult(us_pct=Decimal("62.0"), as_of="2026-07-31", source="cache"),
        target=target,
        band_pct=Decimal(band_pct),
        accounts=accounts,
    )


def trade(account_name, fund_type, fund_name, action, amount):
    return Trade(
        account_name=account_name,
        fund_type=fund_type,
        fund_name=fund_name,
        action=action,
        amount=Decimal(amount),
    )


class TestSummarizeAllocation:
    def test_computes_current_and_target_amounts(self):
        accounts = [
            Account(
                account_type="Roth IRA",
                name="Roth",
                tax_treatment=TaxTreatment.TAX_DEFERRED,
                holdings=[
                    Holding(fund_type=FundType.US_STOCK, name="VTI", value=Decimal(8000)),
                    Holding(fund_type=FundType.US_BOND, name="BND", value=Decimal(2000)),
                ],
            )
        ]
        target = TargetAllocation(
            us_stock_pct=Decimal(60), international_stock_pct=Decimal(20), bond_pct=Decimal(20)
        )
        summary = summarize_allocation(accounts, target)
        assert summary.total_value == Decimal("10000.00")
        assert summary.available_cash == Decimal(0)

        us_stocks = next(c for c in summary.categories if c.label == "U.S. stocks")
        assert us_stocks.current_amount == Decimal("8000.00")
        assert us_stocks.current_pct == Decimal(80)
        assert us_stocks.target_amount == Decimal("6000.00")
        assert us_stocks.target_pct == Decimal(60)

    def test_includes_available_cash_in_total_but_not_any_category(self):
        accounts = [
            Account(
                account_type="Taxable Brokerage",
                name="Brokerage",
                tax_treatment=TaxTreatment.TAXABLE,
                holdings=[
                    Holding(fund_type=FundType.US_STOCK, name="VTI", value=Decimal(1000)),
                    Holding(fund_type=FundType.CASH, name="", value=Decimal(500)),
                ],
            )
        ]
        target = TargetAllocation(
            us_stock_pct=Decimal(100), international_stock_pct=Decimal(0), bond_pct=Decimal(0)
        )
        summary = summarize_allocation(accounts, target)
        assert summary.total_value == Decimal("1500.00")
        assert summary.available_cash == Decimal("500.00")

    def test_empty_portfolio_does_not_divide_by_zero(self):
        summary = summarize_allocation(
            [], TargetAllocation(us_stock_pct=Decimal(60), international_stock_pct=Decimal(20), bond_pct=Decimal(20))
        )
        assert summary.total_value == Decimal(0)
        assert all(c.current_pct == Decimal(0) for c in summary.categories)


class TestDescribeAccountTrades:
    def test_single_sell_and_buy_becomes_exchange(self):
        trades = [
            trade("Roth", FundType.US_STOCK, "VTI", "sell", "400.00"),
            trade("Roth", FundType.US_BOND, "BND", "buy", "400.00"),
        ]
        lines = describe_account_trades(trades)
        assert lines == ["Exchange $400.00 from VTI to BND"]

    def test_multiple_sells_and_one_buy_stay_separate(self):
        trades = [
            trade("Roth", FundType.US_STOCK, "VTI", "sell", "200.00"),
            trade("Roth", FundType.INTERNATIONAL_STOCK, "VXUS", "sell", "200.00"),
            trade("Roth", FundType.US_BOND, "BND", "buy", "400.00"),
        ]
        lines = describe_account_trades(trades)
        assert lines == [
            "Sell $200.00 of VTI",
            "Sell $200.00 of VXUS",
            "Buy $400.00 of BND",
        ]

    def test_single_buy_only_stays_a_buy_line(self):
        trades = [trade("Brokerage", FundType.US_STOCK, "VTI", "buy", "750.00")]
        assert describe_account_trades(trades) == ["Buy $750.00 of VTI"]


class TestGroupTradesByAccount:
    def test_groups_by_account_name(self):
        trades = [
            trade("A", FundType.US_STOCK, "VTI", "sell", "100"),
            trade("B", FundType.US_STOCK, "VTI", "buy", "100"),
            trade("A", FundType.US_BOND, "BND", "buy", "100"),
        ]
        grouped = group_trades_by_account(trades)
        assert set(grouped.keys()) == {"A", "B"}
        assert len(grouped["A"]) == 2
        assert len(grouped["B"]) == 1


class TestFormatReport:
    def make_account_and_target(self):
        account = Account(
            account_type="Roth IRA",
            name="Roth",
            tax_treatment=TaxTreatment.TAX_DEFERRED,
            holdings=[
                Holding(fund_type=FundType.US_STOCK, name="VTI", value=Decimal(1000)),
                Holding(fund_type=FundType.US_BOND, name="BND", value=Decimal(0)),
            ],
        )
        target = TargetAllocation(
            us_stock_pct=Decimal(50), international_stock_pct=Decimal(0), bond_pct=Decimal(50)
        )
        return account, target

    def test_no_trades_message(self):
        account, target = self.make_account_and_target()
        result = RebalanceResult(trades=[], warnings=[], taxable_bond_dollars=Decimal(0))
        text = format_report(inputs([account], target), result)
        assert "already matches your target allocation" in text

    def test_trades_grouped_under_account_header(self):
        account, target = self.make_account_and_target()
        result = RebalanceResult(
            trades=[
                trade("Roth", FundType.US_STOCK, "VTI", "sell", "500.00"),
                trade("Roth", FundType.US_BOND, "BND", "buy", "500.00"),
            ],
            warnings=[],
            taxable_bond_dollars=Decimal(0),
        )
        text = format_report(inputs([account], target), result)
        assert format_account_heading("Roth", "Roth IRA") in text
        assert "Exchange $500.00 from VTI to BND" in text

    def test_warnings_are_included(self):
        account, target = self.make_account_and_target()
        result = RebalanceResult(trades=[], warnings=["Something to flag."], taxable_bond_dollars=Decimal(0))
        text = format_report(inputs([account], target), result)
        assert "Warning: Something to flag." in text

    def test_accounts_with_no_trades_are_omitted_from_the_trade_listing(self):
        account_with_trades, target = self.make_account_and_target()
        account_without_trades = Account(
            account_type="Traditional IRA",
            name="Trad IRA",
            tax_treatment=TaxTreatment.TAX_DEFERRED,
            holdings=[Holding(fund_type=FundType.US_STOCK, name="VTI", value=Decimal(500))],
        )
        result = RebalanceResult(
            trades=[trade("Roth", FundType.US_STOCK, "VTI", "sell", "500.00")],
            warnings=[],
            taxable_bond_dollars=Decimal(0),
        )
        text = format_report(inputs([account_with_trades, account_without_trades], target), result)
        assert format_account_heading("Roth", "Roth IRA") in text
        # The account recap above lists every account whether it trades or
        # not, so this has to look at the trade listing specifically.
        trade_listing = text.split("Orders to place")[1]
        assert "Trad IRA" not in trade_listing

    def test_cash_investment_note_shown_when_present(self):
        account = Account(
            account_type="Taxable Brokerage",
            name="Brokerage",
            tax_treatment=TaxTreatment.TAXABLE,
            holdings=[
                Holding(fund_type=FundType.US_STOCK, name="VTI", value=Decimal(0)),
                Holding(fund_type=FundType.CASH, name="", value=Decimal(1000)),
            ],
        )
        target = TargetAllocation(
            us_stock_pct=Decimal(100), international_stock_pct=Decimal(0), bond_pct=Decimal(0)
        )
        result = RebalanceResult(
            trades=[trade("Brokerage", FundType.US_STOCK, "VTI", "buy", "1000.00")],
            warnings=[],
            taxable_bond_dollars=Decimal(0),
        )
        text = format_report(inputs([account], target), result)
        assert "includes investing $1,000.00 of available cash" in text


class TestReportRecap:
    """The report is the program's output, read on its own with no
    scrollback, so it restates every answer it was computed from."""

    def _accounts(self):
        return [
            Account(
                account_type="Roth IRA",
                name="My Roth",
                tax_treatment=TaxTreatment.TAX_FREE,
                holdings=[
                    Holding(fund_type=FundType.US_STOCK, name="VTI", value=Decimal(6000)),
                    Holding(fund_type=FundType.CASH, name="", value=Decimal(500)),
                ],
            ),
            Account(
                account_type="Taxable Brokerage",
                name="Brokerage",
                tax_treatment=TaxTreatment.TAXABLE,
                holdings=[
                    Holding(fund_type=FundType.INTERNATIONAL_STOCK, name="VXUS", value=Decimal(3500)),
                ],
            ),
        ]

    def _target(self):
        return TargetAllocation(
            us_stock_pct=Decimal(50), international_stock_pct=Decimal(30), bond_pct=Decimal(20)
        )

    def _report(self, band_pct="5"):
        result = RebalanceResult(trades=[], warnings=[], taxable_bond_dollars=Decimal(0))
        return format_report(inputs(self._accounts(), self._target(), band_pct), result)

    def test_restates_the_target_allocation_and_where_it_came_from(self):
        text = self._report()
        assert "Target asset allocation" in text
        assert "50.0%" in text and "30.0%" in text and "20.0%" in text
        # The provenance line is wrapped, so match it with the line breaks
        # collapsed rather than pinning where the wrap happens to fall.
        assert "62.0% U.S. allocation" in " ".join(text.split())
        assert "2026-07-31" in text

    def test_restates_the_rebalancing_band(self):
        assert "Plus or minus 5.0 percentage points" in self._report()

    def test_says_so_plainly_when_there_is_no_band(self):
        assert "exact target" in self._report(band_pct="0")

    def test_lists_every_account_with_its_holdings_and_tax_treatment(self):
        text = self._report()
        assert format_account_heading("My Roth", "Roth IRA, tax-free") in text
        assert "VTI (U.S. stock fund)" in text
        assert "$6,000.00" in text
        assert "Cash available to invest" in text
        assert "$6,500.00" in text

    def test_names_the_tax_treatment_only_when_the_account_type_does_not(self):
        """"Fidelity Brokerage (Taxable Brokerage) -- taxable" says it twice."""
        text = self._report()
        assert "(Roth IRA, tax-free)" in text
        assert "(Taxable Brokerage)" in text
        assert "taxable)" not in text.replace("(Taxable Brokerage)", "")

    def test_a_position_holding_nothing_is_shown_as_a_dash_not_as_zero(self):
        """It is capacity the solver can use, not a holding; "$0.00" gives it
        a precision it does not have."""
        accounts = self._accounts()
        accounts[1].holdings.append(
            Holding(fund_type=FundType.US_BOND, name="BND", value=Decimal(0))
        )
        result = RebalanceResult(trades=[], warnings=[], taxable_bond_dollars=Decimal(0))
        text = format_report(inputs(accounts, self._target(), "5"), result)
        account_block = text.split("Your accounts")[1].split("Current vs. target")[0]
        assert "BND (bond fund)" in account_block
        assert "--" in account_block
        assert "$0.00" not in account_block

    def test_the_comparison_is_a_table_with_aligned_columns(self):
        text = self._report()
        rows = [
            line for line in text.split("\n")
            if line.startswith("  ") and "(" in line and "%)" in line and "$" in line
        ]
        assert len(rows) == 3
        # The money cells are right-aligned, so they line up at their right
        # edge -- which is what lets you read the column down the page.
        assert len({line.index("%)") for line in rows}) == 1
        assert len({len(line.rstrip(" *")) for line in rows}) == 1

    def test_shows_drift_against_target_and_flags_what_sits_outside_the_band(self):
        text = self._report()
        # $6,000 of $10,000 is 60% U.S. against a 50% target: 10 points out.
        assert "+10.0 *" in text
        # $3,500 is 35% international against 30%: 5 points, just inside.
        assert "+5.0\n" in text or "+5.0 " in text
        assert "outside your band of plus or minus 5.0 percentage points" in " ".join(text.split())

    def test_no_band_means_no_footnote_to_explain(self):
        text = self._report(band_pct="0")
        assert "+10.0" in text
        assert "outside your band of" not in text

    def test_no_trades_message_names_the_band(self):
        assert "within your band of plus or minus 5.0 percentage points" in " ".join(self._report().split())

    def test_every_line_fits_the_page_width(self):
        """Four different widths at once was the thing that made this output
        hard to read; prose, table and warnings all answer to a width."""
        result = RebalanceResult(
            trades=[trade("My Roth", FundType.US_STOCK, "VTI", "sell", "500.00")],
            warnings=["A warning long enough to need wrapping. " * 8],
            taxable_bond_dollars=Decimal(0),
        )
        text = format_report(inputs(self._accounts(), self._target(), "5"), result)
        assert max(len(line) for line in text.split("\n")) <= table_width()

    def _long_named_report(self):
        """A nickname at the cap and a fund entered by its full name rather
        than its ticker -- the two things that used to run off the page."""
        account = Account(
            account_type="Traditional 401(k)",
            name="X" * MAX_ACCOUNT_NAME_LENGTH,
            tax_treatment=TaxTreatment.TAX_DEFERRED,
            holdings=[
                Holding(
                    fund_type=FundType.US_STOCK,
                    name="Vanguard Total Stock Market Index Fund Admiral Shares",
                    value=Decimal(6000),
                ),
                Holding(fund_type=FundType.US_BOND, name="BND", value=Decimal(4000)),
            ],
        )
        result = RebalanceResult(
            trades=[
                trade(
                    account.name,
                    FundType.US_STOCK,
                    "Vanguard Total Stock Market Index Fund Admiral Shares",
                    "sell",
                    "1000.00",
                ),
                trade(account.name, FundType.US_BOND, "BND", "buy", "1000.00"),
            ],
            warnings=[],
            taxable_bond_dollars=Decimal(0),
        )
        return format_report(inputs([account], self._target()), result)

    def test_long_names_do_not_push_prose_or_headings_off_the_page(self):
        text = self._long_named_report()
        before, _, rest = text.partition("Your accounts")
        _holdings, _, after = rest.partition("Current vs. target")
        for line in (before + after).split("\n"):
            assert len(line) <= prose_width(), line

    def test_an_order_naming_a_fund_in_full_wraps_with_its_continuation_set_in(self):
        """The order line is prose, not a column, so it can wrap -- and the
        continuation is indented so a run of orders still reads as a list."""
        text = self._long_named_report()
        orders = text.split("Review each order before placing it:")[1]
        wrapped = [line for line in orders.split("\n") if line.startswith(INDENT_UNIT * 3)]
        assert wrapped, "expected the long order line to wrap"

    def test_the_holdings_table_is_allowed_to_run_wide(self):
        """A fund's real name can be longer than the page, and truncating it
        is how someone buys the wrong fund -- they search that name at the
        broker. The table keeps its columns and runs wide instead."""
        text = self._long_named_report()
        holdings_block = text.split("Your accounts")[1].split("Current vs. target")[0]
        rows = [
            line
            for line in holdings_block.split("\n")
            if line.startswith(INDENT_UNIT * 2) and line.strip().endswith((".00", "--"))
        ]
        assert any(len(line) > prose_width() for line in rows)
        # Wide, but still a table: every amount ends in the same column.
        assert len({len(line) for line in rows}) == 1


class TestOutcomeLine:
    def test_says_where_the_trades_land(self):
        account = Account(
            account_type="Roth IRA",
            name="Roth",
            tax_treatment=TaxTreatment.TAX_FREE,
            holdings=[
                Holding(fund_type=FundType.US_STOCK, name="VTI", value=Decimal(1000)),
                Holding(fund_type=FundType.US_BOND, name="BND", value=Decimal(0)),
            ],
        )
        target = TargetAllocation(
            us_stock_pct=Decimal(50), international_stock_pct=Decimal(0), bond_pct=Decimal(50)
        )
        result = RebalanceResult(
            trades=[
                trade("Roth", FundType.US_STOCK, "VTI", "sell", "500.00"),
                trade("Roth", FundType.US_BOND, "BND", "buy", "500.00"),
            ],
            warnings=[],
            taxable_bond_dollars=Decimal(0),
        )
        text = format_report(inputs([account], target), result)
        assert "After these trades: 50.0% U.S. / 0.0% international / 50.0% bonds" in text

    def test_a_target_date_sleeve_moves_by_its_own_fractions(self):
        allocation = TargetDateAllocation(
            us_stock_pct=Decimal(60),
            international_stock_pct=Decimal(20),
            bond_pct=Decimal(20),
        )
        account = Account(
            account_type="Roth 401(k)",
            name="401k",
            tax_treatment=TaxTreatment.TAX_FREE,
            holdings=[
                Holding(
                    fund_type=FundType.TARGET_DATE,
                    name="Target 2050",
                    value=Decimal(900),
                    target_date_allocation=allocation,
                ),
                Holding(fund_type=FundType.CASH, name="", value=Decimal(100)),
            ],
        )
        after = allocation_after_trades(
            [account], [trade("401k", FundType.TARGET_DATE, "Target 2050", "buy", "100.00")]
        )
        assert after["U.S. stocks"] == Decimal(600)
        assert after["International stocks"] == Decimal(200)
        assert after["Bonds"] == Decimal(200)


class TestExchangeCollapsing:
    def test_unequal_legs_are_never_collapsed_into_one_exchange(self):
        """An account with cash to invest sells less than it buys. Collapsing
        that into "Exchange $X from A to B" states the sell amount for both
        legs, and the user under-buys by exactly the cash they were told to
        put to work."""
        lines = describe_account_trades([
            trade("401k", FundType.US_STOCK, "VTI", "sell", "16937.00"),
            trade("401k", FundType.US_BOND, "BND", "buy", "17437.00"),
        ])
        assert lines == ["Sell $16,937.00 of VTI", "Buy $17,437.00 of BND"]


class TestRequiredWording:
    """Compliance-driven wording, pinned so an edit that reads better but
    loses it fails rather than shipping."""

    def _account(self, treatment=TaxTreatment.TAX_FREE, account_type="Roth IRA"):
        return Account(
            account_type=account_type,
            name="Roth",
            tax_treatment=treatment,
            holdings=[
                Holding(fund_type=FundType.US_STOCK, name="VTI", value=Decimal(1000)),
                Holding(fund_type=FundType.US_BOND, name="BND", value=Decimal(1000)),
            ],
        )

    def _target(self):
        return TargetAllocation(
            us_stock_pct=Decimal(50), international_stock_pct=Decimal(0), bond_pct=Decimal(50)
        )

    def _report(self, result=None, **kwargs):
        result = result or RebalanceResult(
            trades=[], warnings=[], taxable_bond_dollars=Decimal(0)
        )
        report_inputs = replace(inputs([self._account()], self._target()), **kwargs)
        return format_report(report_inputs, result)

    def test_the_disclaimer_always_travels_with_the_report(self):
        assert "Not investment or tax advice" in self._report()

    def test_the_disclaimer_survives_a_report_that_has_trades_and_warnings(self):
        result = RebalanceResult(
            trades=[trade("Roth", FundType.US_STOCK, "VTI", "sell", "100.00")],
            warnings=["Something to flag."],
            taxable_bond_dollars=Decimal(0),
        )
        text = self._report(result)
        assert text.rstrip().endswith("situation.")

    def test_no_order_is_phrased_as_an_instruction(self):
        """"Recommendation" is a term of art, and "Place the following
        orders" instructs rather than describes."""
        result = RebalanceResult(
            trades=[trade("Roth", FundType.US_STOCK, "VTI", "sell", "100.00")],
            warnings=[],
            taxable_bond_dollars=Decimal(0),
        )
        text = self._report(result)
        assert "Orders to place" in text
        assert "Review each order before placing it:" in text
        assert "Recommended trades" not in text
        assert "Place the following orders" not in text

    def test_tax_free_is_qualified_where_it_is_used(self):
        assert '"Tax-free" means qualified withdrawals' in self._report()

    def test_tax_free_is_not_qualified_when_no_account_is_tax_free(self):
        report_inputs = replace(
            inputs([self._account(TaxTreatment.TAXABLE, "Taxable Brokerage")], self._target())
        )
        text = format_report(
            report_inputs, RebalanceResult(trades=[], warnings=[], taxable_bond_dollars=Decimal(0))
        )
        assert "qualified withdrawals" not in text

    def test_figures_say_they_came_from_the_user(self):
        assert "Values as entered." in self._report()

    def test_figures_name_the_last_saved_date_when_there_is_one(self):
        assert "Values as entered; last saved 2026-08-21." in self._report(
            values_as_of="2026-08-21"
        )

    def test_dropped_sub_minimum_moves_are_disclosed(self):
        result = RebalanceResult(
            trades=[trade("Roth", FundType.US_STOCK, "VTI", "sell", "100.00")],
            warnings=[],
            taxable_bond_dollars=Decimal(0),
            dropped_trades=2,
        )
        # Wrapped prose, so match it with the line breaks collapsed.
        text = " ".join(self._report(result).split())
        assert "2 moves smaller than $1.00 were left out as impractical" in text
        assert "these orders do not reach the target exactly" in text

    def test_nothing_is_disclosed_when_nothing_was_dropped(self):
        result = RebalanceResult(
            trades=[trade("Roth", FundType.US_STOCK, "VTI", "sell", "100.00")],
            warnings=[],
            taxable_bond_dollars=Decimal(0),
        )
        assert "left out as impractical" not in self._report(result)


class TestIndentation:
    """Depth is the whole hierarchy below a subheading, so two listings of
    the same kind of thing have to sit at the same depth."""

    def _report(self):
        accounts = [
            Account(
                account_type="Roth IRA",
                name="Roth",
                tax_treatment=TaxTreatment.TAX_FREE,
                holdings=[
                    Holding(fund_type=FundType.US_STOCK, name="VTI", value=Decimal(6000)),
                    Holding(fund_type=FundType.US_BOND, name="BND", value=Decimal(4000)),
                ],
            )
        ]
        target = TargetAllocation(
            us_stock_pct=Decimal(50), international_stock_pct=Decimal(0), bond_pct=Decimal(50)
        )
        result = RebalanceResult(trades=[], warnings=[], taxable_bond_dollars=Decimal(0))
        return format_report(inputs(accounts, target, "5"), result)

    def _depth(self, text, needle):
        line = next(line for line in text.split("\n") if needle in line)
        return len(line) - len(line.lstrip())

    def test_both_asset_class_listings_sit_at_the_same_depth(self):
        text = self._report()
        target_row = self._depth(text, "U.S. stocks            50.0%")
        comparison_row = self._depth(text, "U.S. stocks  ")
        assert target_row == comparison_row == len(INDENT_UNIT)

    def test_holdings_sit_one_level_in_from_their_account(self):
        text = self._report()
        assert self._depth(text, "Roth (Roth IRA, tax-free)") == len(INDENT_UNIT)
        assert self._depth(text, "VTI (U.S. stock fund)") == len(INDENT_UNIT) * 2

    def test_a_section_footnote_does_not_align_with_the_account_headings(self):
        """At one level in it reads as a third account rather than a note."""
        text = self._report()
        assert self._depth(text, "means qualified withdrawals") == 0


class TestPercentFormatting:
    """Two rules, one for each side of the program: the report fixes every
    percentage at one decimal place, prompts trim trailing zeros. A distance
    between two percentages is "percentage points" -- "pts" only in the
    comparison table header, where the column cannot take the words."""

    def _report(self, band_pct="5", stock=Decimal(80), bond=Decimal(20)):
        accounts = [
            Account(
                account_type="Roth IRA",
                name="Roth",
                tax_treatment=TaxTreatment.TAX_FREE,
                holdings=[
                    Holding(fund_type=FundType.US_STOCK, name="VTI", value=Decimal(8000)),
                    Holding(fund_type=FundType.US_BOND, name="BND", value=Decimal(2000)),
                ],
            )
        ]
        target = TargetAllocation(
            us_stock_pct=Decimal(50), international_stock_pct=Decimal(30), bond_pct=Decimal(20)
        )
        report_inputs = replace(inputs(accounts, target, band_pct), stock_pct=stock, bond_pct=bond)
        return format_report(
            report_inputs, RebalanceResult(trades=[], warnings=[], taxable_bond_dollars=Decimal(0))
        )

    def test_every_percentage_carries_one_decimal_place(self):
        """Whole numbers included -- "20% bonds" two lines under "20.0%" is
        the inconsistency this rule exists to stop."""
        text = " ".join(self._report().split())
        assert "From 80.0% stocks / 20.0% bonds" in text
        assert "VT's 62.0% U.S. allocation" in text
        for bare in ("80% ", "20% ", "62% "):
            assert bare not in text

    def test_a_distance_between_percentages_is_called_percentage_points(self):
        text = " ".join(self._report().split())
        assert "Plus or minus 5.0 percentage points" in text
        assert "band of plus or minus 5.0 percentage points" in text
        assert "point band" not in text
        assert "percentage point rebalancing band" not in text

    def test_pts_is_used_only_in_the_table_header(self):
        assert self._report().count("pts") == 1
        assert "Drift (pts)" in self._report()


class TestFormatPercent:
    def test_trims_trailing_zeros_so_a_default_reads_as_typed(self):
        assert format_percent(Decimal("62.0")) == "62"
        assert format_percent(Decimal(80)) == "80"
        assert format_percent(Decimal("5.0")) == "5"

    def test_keeps_significant_decimals(self):
        assert format_percent(Decimal("61.9")) == "61.9"
        assert format_percent(Decimal("34.34")) == "34.34"

    def test_never_falls_back_to_exponent_notation(self):
        """Decimal("100").normalize() is 1E+2, which is not a percentage
        anyone wants offered as a default."""
        assert format_percent(Decimal(100)) == "100"


class TestTerminalWidth:
    """Prose follows the terminal up to a readable maximum; tables follow it
    without one."""

    def test_prose_and_tables_both_follow_a_wider_terminal(self, monkeypatch):
        """Below the cap the two agree; pick the width from the cap so this
        keeps testing what it means if PROSE_MAX_WIDTH is retuned."""
        monkeypatch.setenv("COLUMNS", str(PROSE_MAX_WIDTH))
        assert prose_width() == table_width() == PROSE_MAX_WIDTH - 2

    def test_prose_stops_at_the_readable_maximum(self, monkeypatch):
        monkeypatch.setenv("COLUMNS", "200")
        assert prose_width() == PROSE_MAX_WIDTH

    def test_tables_keep_going_past_it(self, monkeypatch):
        monkeypatch.setenv("COLUMNS", "200")
        assert table_width() == 198

    def test_a_narrow_terminal_narrows_both(self, monkeypatch):
        monkeypatch.setenv("COLUMNS", "60")
        assert prose_width() == 58
        assert table_width() == 58

    def _report(self):
        account = Account(
            account_type="Roth IRA",
            name="Roth",
            tax_treatment=TaxTreatment.TAX_FREE,
            holdings=[
                Holding(fund_type=FundType.US_STOCK, name="VTI", value=Decimal(6000)),
                Holding(fund_type=FundType.US_BOND, name="BND", value=Decimal(4000)),
            ],
        )
        target = TargetAllocation(
            us_stock_pct=Decimal(50), international_stock_pct=Decimal(0), bond_pct=Decimal(50)
        )
        result = RebalanceResult(
            trades=[], warnings=["A warning long enough to need wrapping. " * 6],
            taxable_bond_dollars=Decimal(0),
        )
        return format_report(inputs([account], target), result)

    def test_the_report_respects_whatever_width_is_in_force(self, monkeypatch):
        """Asserted against the width itself rather than against a specific
        wrap outcome: with the cap near the default terminal the two differ
        by only a couple of columns, so pinning exact line lengths would
        break on any wording change."""
        monkeypatch.setenv("COLUMNS", "80")
        narrow_limit, narrow = prose_width(), self._report()
        monkeypatch.setenv("COLUMNS", "120")
        wide_limit, wide = prose_width(), self._report()

        assert narrow_limit < wide_limit
        assert max(len(line) for line in narrow.split("\n")) <= narrow_limit
        assert max(len(line) for line in wide.split("\n")) <= wide_limit

    def test_a_seven_figure_portfolio_no_longer_overflows(self, monkeypatch):
        """The dollar columns are four characters wider than a five-figure
        portfolio's, which used to push the comparison table past a fixed 78."""
        monkeypatch.setenv("COLUMNS", "100")
        accounts = [
            Account(
                account_type="Taxable Brokerage",
                name="Brokerage",
                tax_treatment=TaxTreatment.TAXABLE,
                holdings=[
                    Holding(fund_type=FundType.US_STOCK, name="VTI", value=Decimal(9_000_000)),
                    Holding(
                        fund_type=FundType.INTERNATIONAL_STOCK,
                        name="VXUS",
                        value=Decimal(1_000_000),
                    ),
                ],
            )
        ]
        target = TargetAllocation(
            us_stock_pct=Decimal(60), international_stock_pct=Decimal(40), bond_pct=Decimal(0)
        )
        result = RebalanceResult(trades=[], warnings=[], taxable_bond_dollars=Decimal(0))
        text = format_report(inputs(accounts, target), result)
        assert max(len(line) for line in text.split("\n")) <= table_width()
