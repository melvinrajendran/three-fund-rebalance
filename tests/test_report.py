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
    wrap,
)
from three_fund_rebalance.models import (
    Account,
    FundType,
    Holding,
    RebalanceResult,
    TargetAllocation,
    TargetDateAllocation,
    TaxTreatment,
    Trade,
)
from three_fund_rebalance.report import (
    DISCLAIMER,
    RebalanceInputs,
    allocation_after_trades,
    describe_account_trades,
    format_report,
    group_trades_by_account,
    summarize_allocation,
)
from three_fund_rebalance.vt_allocation import VTAllocationResult


def inputs(accounts, target, band_pct="0", relative_band_pct=None):
    """The report recaps what it was asked, so it needs the whole set of
    answers -- not just the accounts and the target it computes against."""
    return RebalanceInputs(
        stock_pct=target.us_stock_pct + target.international_stock_pct,
        bond_pct=target.bond_pct,
        vt=VTAllocationResult(us_pct=Decimal("62.0"), as_of="2026-07-31", source="cache"),
        target=target,
        band_pct=Decimal(band_pct),
        accounts=accounts,
        relative_band_pct=None if relative_band_pct is None else Decimal(relative_band_pct),
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
                account_type="Brokerage",
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
            [],
            TargetAllocation(
                us_stock_pct=Decimal(60),
                international_stock_pct=Decimal(20),
                bond_pct=Decimal(20),
            ),
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
        # On target to the dollar: the other line only appears when a class
        # is outside its band, which with no band means off target at all.
        _, target = self.make_account_and_target()
        on_target = Account(
            account_type="Roth IRA",
            name="Roth",
            tax_treatment=TaxTreatment.TAX_DEFERRED,
            holdings=[
                Holding(fund_type=FundType.US_STOCK, name="VTI", value=Decimal(500)),
                Holding(fund_type=FundType.US_BOND, name="BND", value=Decimal(500)),
            ],
        )
        result = RebalanceResult(trades=[], warnings=[], taxable_bond_dollars=Decimal(0))
        text = format_report(inputs([on_target], target), result)
        assert "already matches the target allocation" in text

    def test_no_trades_message_when_a_class_cannot_reach_its_band(self):
        """A portfolio with nothing to trade and a class still outside its
        band is neither on target nor inside the band: what the accounts can
        hold is what stopped it, and the report may not claim otherwise."""
        account, target = self.make_account_and_target()  # all stock, half-bond target
        result = RebalanceResult(trades=[], warnings=[], taxable_bond_dollars=Decimal(0))
        text = " ".join(format_report(inputs([account], target), result).split())
        assert "as close to the target allocation as the funds held allow" in text
        assert "already matches the target allocation" not in text

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
        result = RebalanceResult(
            trades=[], warnings=["Something to flag."], taxable_bond_dollars=Decimal(0)
        )
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
        trade_listing = text.split("Orders to Place")[1]
        assert "Trad IRA" not in trade_listing

    def test_cash_investment_note_shown_when_present(self):
        account = Account(
            account_type="Brokerage",
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
                account_type="Brokerage",
                name="Brokerage",
                tax_treatment=TaxTreatment.TAXABLE,
                holdings=[
                    Holding(
                        fund_type=FundType.INTERNATIONAL_STOCK, name="VXUS", value=Decimal(3500)
                    ),
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
        assert "Target Asset Allocation" in text
        assert "50%" in text and "30%" in text and "20%" in text
        # The provenance line is wrapped, so match it with the line breaks
        # collapsed rather than pinning where the wrap happens to fall.
        assert "62% U.S. allocation" in " ".join(text.split())
        assert "July 31, 2026" in text

    def test_restates_the_rebalancing_band(self):
        assert "Plus or minus 5 percentage points" in self._report()

    def test_says_so_plainly_when_there_is_no_band(self):
        assert "exact target" in self._report(band_pct="0")

    def test_lists_every_account_with_its_holdings_and_tax_treatment(self):
        text = self._report()
        assert format_account_heading("My Roth", "Roth IRA, tax-free") in text
        assert "VTI (U.S. stock fund)" in text
        assert "$6,000.00" in text
        assert "Cash available to invest" in text
        assert "$6,500.00" in text

    def test_every_account_heading_has_the_same_shape(self):
        """`nickname (type, treatment)`, with no exceptions -- one line shaped
        like the next is what lets the eye compare them down the page."""
        text = self._report()
        assert "(Roth IRA, tax-free)" in text
        assert "(Brokerage, taxable)" in text

    def test_a_position_holding_nothing_is_shown_as_a_dash_not_as_zero(self):
        """It is capacity the solver can use, not a holding; "$0.00" gives it
        a precision it does not have."""
        accounts = self._accounts()
        accounts[1].holdings.append(
            Holding(fund_type=FundType.US_BOND, name="BND", value=Decimal(0))
        )
        result = RebalanceResult(trades=[], warnings=[], taxable_bond_dollars=Decimal(0))
        text = format_report(inputs(accounts, self._target(), "5"), result)
        account_block = text.split("Account Holdings")[1].split("Current vs. Target")[0]
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

    def test_the_cents_line_up_in_every_money_column(self):
        """The dollars and the share beside them are two columns, not one
        cell: aligned as one string, a five-figure amount next to a
        six-figure one lines up on whatever trails it and the cents wander."""
        accounts = self._accounts()
        accounts[0].holdings[0] = replace(accounts[0].holdings[0], value=Decimal(100_000))
        result = RebalanceResult(trades=[], warnings=[], taxable_bond_dollars=Decimal(0))
        text = format_report(inputs(accounts, self._target(), "5"), result)
        rows = [
            line for line in text.split("\n")
            if line.startswith("  ") and "%)" in line and "$" in line
        ]
        assert len(rows) == 3
        # The cents of both money columns fall in the same two places on
        # every row, whatever else the row is wide enough to hold.
        cents = [
            tuple(line.index(".", i) for i, c in enumerate(line) if c == "$") for line in rows
        ]
        assert all(len(places) == 2 for places in cents)
        assert len(set(cents)) == 1

    def test_shows_drift_against_target_and_flags_what_sits_outside_the_band(self):
        text = self._report()
        # $6,000 of $10,000 is 60% U.S. against a 50% target: 10 points out.
        assert "+10 *" in text
        # $3,500 is 35% international against 30%: 5 points, just inside.
        assert "+5\n" in text or "+5 " in text
        assert "outside the band of plus or minus 5 percentage points" in " ".join(text.split())

    def test_no_band_means_no_footnote_to_explain(self):
        text = self._report(band_pct="0")
        assert "+10" in text
        assert "outside the band of" not in text

    def test_no_trades_message_names_the_band(self):
        # Sitting on the target, so the band is what the line has to name.
        in_band = [
            Account(
                account_type="Roth IRA",
                name="My Roth",
                tax_treatment=TaxTreatment.TAX_FREE,
                holdings=[
                    Holding(fund_type=FundType.US_STOCK, name="VTI", value=Decimal(5000)),
                    Holding(
                        fund_type=FundType.INTERNATIONAL_STOCK, name="VXUS", value=Decimal(3000)
                    ),
                    Holding(fund_type=FundType.US_BOND, name="BND", value=Decimal(2000)),
                ],
            )
        ]
        result = RebalanceResult(trades=[], warnings=[], taxable_bond_dollars=Decimal(0))
        rendered = " ".join(
            format_report(inputs(in_band, self._target(), "5"), result).split()
        )
        assert "within the band of plus or minus 5 percentage points" in rendered

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
        before, _, rest = text.partition("Account Holdings")
        _holdings, _, after = rest.partition("Current vs. Target")
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
        holdings_block = text.split("Account Holdings")[1].split("Current vs. Target")[0]
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
        assert (
            "If these orders fill at the values entered here, the portfolio will hold "
            "50% U.S. stocks, 0% international stocks, and 50% bonds."
            in " ".join(text.split())
        )

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
        text = self._report()
        assert "Not investment, tax, or legal advice" in " ".join(text.split())

    def test_the_disclaimer_survives_a_report_that_has_trades_and_warnings(self):
        result = RebalanceResult(
            trades=[trade("Roth", FundType.US_STOCK, "VTI", "sell", "100.00")],
            warnings=["Something to flag."],
            taxable_bond_dollars=Decimal(0),
        )
        text = self._report(result)
        assert text.rstrip().endswith("situation.")

    def test_the_disclaimer_disclaims_a_recommendation(self):
        """"Recommendation" is the Reg BI / FINRA 2111 term of art, so
        disclaiming it is the other half of never using the word above. It is
        the one clause that survived the cut to two lines; the README's
        Disclaimer section carries the rest."""
        text = " ".join(self._report().split())
        assert "not a recommendation to buy or sell" in text

    def test_the_disclaimer_stays_short_enough_to_be_read(self):
        """Eight lines of legal prose at the foot of a page is something a
        reader learns to skip, which costs the disclosure the one thing it is
        there for."""
        assert len(wrap(DISCLAIMER).split("\n")) <= 2

    def test_the_outcome_is_conditional_on_the_orders_filling(self):
        """An order fills at the market's price, not at the figure typed in
        here, so the landing allocation is arithmetic rather than a promise."""
        result = RebalanceResult(
            trades=[trade("Roth", FundType.US_STOCK, "VTI", "sell", "100.00")],
            warnings=[],
            taxable_bond_dollars=Decimal(0),
        )
        text = " ".join(self._report(result).split())
        assert "If these orders fill at the values entered here," in text
        assert "After these trades:" not in text

    def _taxable_report(self, trades):
        account = Account(
            account_type="Brokerage",
            name="Brokerage",
            tax_treatment=TaxTreatment.TAXABLE,
            holdings=[
                Holding(fund_type=FundType.US_STOCK, name="VTI", value=Decimal(1000)),
                Holding(fund_type=FundType.US_BOND, name="BND", value=Decimal(1000)),
            ],
        )
        return format_report(
            inputs([account], self._target()),
            RebalanceResult(trades=trades, warnings=[], taxable_bond_dollars=Decimal(0)),
        )

    def test_a_taxable_sale_is_disclosed_as_a_taxable_event(self):
        """The solver holds taxable selling down but works from dollars
        traded, not cost basis -- so the report says the sale is realizable
        without implying it can price it."""
        text = " ".join(self._taxable_report([
            trade("Brokerage", FundType.US_STOCK, "VTI", "sell", "100.00"),
            trade("Brokerage", FundType.US_BOND, "BND", "buy", "100.00"),
        ]).split())
        assert "Selling $100.00 in taxable accounts" in text
        assert "may realize capital gains or losses" in text
        assert "no cost basis is collected here, so that tax is not estimated" in text

    def test_a_taxable_purchase_alone_is_not_a_taxable_event(self):
        """Only the sale leg realizes anything. Warning on a buy would train
        the reader to scroll past the note that matters."""
        text = self._taxable_report([
            trade("Brokerage", FundType.US_BOND, "BND", "buy", "100.00"),
        ])
        assert "capital gains or losses" not in text

    def test_a_sheltered_sale_raises_no_capital_gains_note(self):
        result = RebalanceResult(
            trades=[trade("Roth", FundType.US_STOCK, "VTI", "sell", "100.00")],
            warnings=[],
            taxable_bond_dollars=Decimal(0),
        )
        assert "capital gains or losses" not in self._report(result)

    def test_no_order_is_phrased_as_an_instruction(self):
        """"Recommendation" is a term of art, and "Place the following
        orders" instructs rather than describes."""
        result = RebalanceResult(
            trades=[trade("Roth", FundType.US_STOCK, "VTI", "sell", "100.00")],
            warnings=[],
            taxable_bond_dollars=Decimal(0),
        )
        text = self._report(result)
        assert "Orders to Place" in text
        assert "Review each order before placing it:" in text
        assert "Recommended trades" not in text
        assert "Place the following orders" not in text

    def test_an_asset_class_is_never_shortened_to_a_class(self):
        """Bare "class" in a retail investing context reads as *share*
        class -- Admiral against Investor, Class A against Class C. The
        README's wash-sale limitation uses it in exactly that sense, so a
        warning that lands beside it must not blur the two."""
        result = RebalanceResult(
            trades=[trade("Roth", FundType.US_STOCK, "VTI", "sell", "100.00")],
            warnings=["two share classes of one index (VTI and VTSAX) slip past it"],
            taxable_bond_dollars=Decimal(0),
        )
        text = " ".join(self._report(result, band_pct=Decimal(5)).split())
        for bare in ("a class", "the class", "every class", "each class", "one class"):
            assert bare not in text, bare
        assert "asset class" in text
        # ...and the other sense still says which kind of class it means.
        assert "share classes" in text

    def test_the_report_does_not_gloss_the_tax_treatment_labels(self):
        """The labels are standard shorthand and the plan documents are where
        the conditions on them live; the report states what each account is
        and stops."""
        assert "qualified withdrawals" not in self._report()

    def test_figures_say_they_came_from_the_user(self):
        assert "Values as entered, not live market prices." in self._report()

    def test_figures_name_the_last_saved_date_when_there_is_one(self):
        text = self._report(values_as_of="2026-08-21")
        assert "Values as entered, not live market prices. Last saved August 21, 2026." in text

    def test_dropped_sub_minimum_orders_are_disclosed(self):
        result = RebalanceResult(
            trades=[trade("Roth", FundType.US_STOCK, "VTI", "sell", "100.00")],
            warnings=[],
            taxable_bond_dollars=Decimal(0),
            dropped_trades=2,
        )
        # Wrapped prose, so match it with the line breaks collapsed.
        text = " ".join(self._report(result).split())
        assert "Two orders smaller than $1.00 were left out as impractical" in text
        # "the above orders", not "these orders": with a dropped order named in
        # the same sentence, "these" points at either set.
        assert "the above orders do not reach the target exactly" in text

    def test_a_single_dropped_order_reads_as_a_sentence(self):
        """"1 order ... was left out" opens on a numeral, which reads as a
        fragment. Small counts are spelled out."""
        result = RebalanceResult(
            trades=[trade("Roth", FundType.US_STOCK, "VTI", "sell", "100.00")],
            warnings=[],
            taxable_bond_dollars=Decimal(0),
            dropped_trades=1,
        )
        text = " ".join(self._report(result).split())
        assert "One order smaller than $1.00 was left out as impractical" in text
        assert "1 order" not in text

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
        target_row = self._depth(text, "U.S. stocks           50%")
        comparison_row = self._depth(text, "U.S. stocks  ")
        assert target_row == comparison_row == len(INDENT_UNIT)

    def test_holdings_sit_one_level_in_from_their_account(self):
        text = self._report()
        assert self._depth(text, "Roth (Roth IRA, tax-free)") == len(INDENT_UNIT)
        assert self._depth(text, "VTI (U.S. stock fund)") == len(INDENT_UNIT) * 2



class TestPercentFormatting:
    """Prose carries the precision each figure needs; a table carries the
    precision its column needs, so the figures line up. A distance between
    two percentages is "percentage points" -- "pts" only in the comparison
    table header, where the column cannot take the words."""

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

    def test_prose_writes_each_percentage_as_short_as_it_goes(self):
        """A sentence has no column to line up with, so a whole number is
        written as one."""
        text = " ".join(self._report().split())
        assert "Derived from 80% stocks and 20% bonds" in text
        assert "VT's 62% U.S. allocation" in text
        for padded in ("80.0%", "20.0%", "62.0%"):
            assert padded not in text

    def test_a_table_column_shares_one_precision(self):
        """The figures are read down the page, so they line up on the decimal
        point: a column holding 58.8 writes its 5 as 5.0."""
        accounts = [
            Account(
                account_type="Roth IRA",
                name="Roth",
                tax_treatment=TaxTreatment.TAX_FREE,
                holdings=[Holding(fund_type=FundType.US_STOCK, name="VTI", value=Decimal(10000))],
            )
        ]
        target = TargetAllocation(
            us_stock_pct=Decimal("58.8"),
            international_stock_pct=Decimal("36.2"),
            bond_pct=Decimal(5),
        )
        text = format_report(
            inputs(accounts, target),
            RebalanceResult(trades=[], warnings=[], taxable_bond_dollars=Decimal(0)),
        )
        assert "  U.S. stocks           58.8%" in text
        assert "  International stocks  36.2%" in text
        assert "  Bonds                  5.0%" in text

    def test_a_distance_between_percentages_is_called_percentage_points(self):
        text = " ".join(self._report().split())
        assert "Plus or minus 5 percentage points" in text
        assert "band of plus or minus 5 percentage points" in text
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
        assert format_percent(Decimal("34.56")) == "34.56"

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
                account_type="Brokerage",
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


class TestRelativeBandInTheReport:
    """With two rules meeting at whichever is tighter, each class gets its
    own band -- so the report writes them out instead of naming one number."""

    def _accounts(self):
        return [
            Account(
                account_type="Roth IRA",
                name="My Roth",
                tax_treatment=TaxTreatment.TAX_FREE,
                holdings=[
                    Holding(fund_type=FundType.US_STOCK, name="VTI", value=Decimal(5880)),
                    Holding(
                        fund_type=FundType.INTERNATIONAL_STOCK, name="VXUS", value=Decimal(4000)
                    ),
                    Holding(fund_type=FundType.US_BOND, name="BND", value=Decimal(120)),
                ],
            ),
        ]

    def _target(self):
        return TargetAllocation(
            us_stock_pct=Decimal("58.8"),
            international_stock_pct=Decimal("36.2"),
            bond_pct=Decimal(5),
        )

    def _report(self, relative_band_pct="25"):
        result = RebalanceResult(trades=[], warnings=[], taxable_bond_dollars=Decimal(0))
        return format_report(
            inputs(self._accounts(), self._target(), "5", relative_band_pct), result
        )

    def _band_section(self, text):
        return text.split("Rebalancing Bands")[1].split("Account Holdings")[0]

    def test_states_both_rules_and_which_one_wins(self):
        assert (
            "Plus or minus 5 percentage points, or 25% of an asset class's "
            "target, whichever is tighter"
        ) in " ".join(self._report().split())

    def test_writes_out_each_class_s_own_band(self):
        """A reader should not have to work out that a quarter of 5% is 1.2
        points while a quarter of 58.8% is 14.7."""
        section = self._band_section(self._report())
        assert "U.S. stocks           53.8% to 63.8%" in section
        assert "International stocks  31.2% to 41.2%" in section
        assert "Bonds                  3.8% to  6.2%" in section

    def test_the_ranges_are_omitted_when_only_the_absolute_rule_applies(self):
        section = self._band_section(self._report(relative_band_pct=None))
        assert "Plus or minus 5 percentage points." in " ".join(section.split())
        assert "% to " not in section  # no per-class ranges: one number covers all three

    def test_the_footnote_stops_naming_a_single_band(self):
        """Bonds at 1.2% against a 3.8% floor are out; U.S. stock at 58.8% is
        in. One number cannot describe both, so the footnote points at the
        section that lists them."""
        text = self._report()
        assert "-3.8 *" in text
        assert "* outside its rebalancing band" in text
        assert "percentage points" not in text.split("Current vs. Target")[1]

    def test_the_no_trades_line_stops_naming_a_single_band(self):
        on_target = [
            Account(
                account_type="Roth IRA",
                name="My Roth",
                tax_treatment=TaxTreatment.TAX_FREE,
                holdings=[
                    Holding(fund_type=FundType.US_STOCK, name="VTI", value=Decimal(5880)),
                    Holding(
                        fund_type=FundType.INTERNATIONAL_STOCK, name="VXUS", value=Decimal(3620)
                    ),
                    Holding(fund_type=FundType.US_BOND, name="BND", value=Decimal(500)),
                ],
            ),
        ]
        result = RebalanceResult(trades=[], warnings=[], taxable_bond_dollars=Decimal(0))
        text = format_report(inputs(on_target, self._target(), "5", "25"), result)
        assert "Every asset class is within its rebalancing band -- no trades needed." in (
            " ".join(text.split())
        )
