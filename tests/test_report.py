from decimal import Decimal

from three_fund_rebalance.models import (
    Account,
    FundType,
    Holding,
    TargetAllocation,
    TaxTreatment,
    Trade,
)
from three_fund_rebalance.rebalance import RebalanceResult
from three_fund_rebalance.report import (
    describe_account_trades,
    format_report,
    group_trades_by_account,
    summarize_allocation,
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
                tax_treatment=TaxTreatment.TAX_ADVANTAGED,
                holdings=[
                    Holding(fund_type=FundType.DOMESTIC_EQUITY, name="VTI", balance=Decimal(8000)),
                    Holding(fund_type=FundType.DOMESTIC_BOND, name="BND", balance=Decimal(2000)),
                ],
            )
        ]
        target = TargetAllocation(
            domestic_equity_pct=Decimal(60), international_equity_pct=Decimal(20), bond_pct=Decimal(20)
        )
        summary = summarize_allocation(accounts, target)
        assert summary.total_value == Decimal("10000.00")
        assert summary.uninvested_cash == Decimal(0)

        domestic = next(c for c in summary.categories if c.label == "Domestic equity")
        assert domestic.current_amount == Decimal("8000.00")
        assert domestic.current_pct == Decimal(80)
        assert domestic.target_amount == Decimal("6000.00")
        assert domestic.target_pct == Decimal(60)

    def test_includes_uninvested_cash_in_total_but_not_any_category(self):
        accounts = [
            Account(
                account_type="Taxable Brokerage",
                name="Brokerage",
                tax_treatment=TaxTreatment.TAXABLE,
                holdings=[
                    Holding(fund_type=FundType.DOMESTIC_EQUITY, name="VTI", balance=Decimal(1000)),
                    Holding(fund_type=FundType.CASH, name="", balance=Decimal(500)),
                ],
            )
        ]
        target = TargetAllocation(
            domestic_equity_pct=Decimal(100), international_equity_pct=Decimal(0), bond_pct=Decimal(0)
        )
        summary = summarize_allocation(accounts, target)
        assert summary.total_value == Decimal("1500.00")
        assert summary.uninvested_cash == Decimal("500.00")

    def test_empty_portfolio_does_not_divide_by_zero(self):
        summary = summarize_allocation(
            [], TargetAllocation(domestic_equity_pct=Decimal(60), international_equity_pct=Decimal(20), bond_pct=Decimal(20))
        )
        assert summary.total_value == Decimal(0)
        assert all(c.current_pct == Decimal(0) for c in summary.categories)


class TestDescribeAccountTrades:
    def test_single_sell_and_buy_becomes_exchange(self):
        trades = [
            trade("Roth", FundType.DOMESTIC_EQUITY, "VTI", "sell", "400.00"),
            trade("Roth", FundType.DOMESTIC_BOND, "BND", "buy", "400.00"),
        ]
        lines = describe_account_trades(trades)
        assert lines == ["Exchange $400.00 from VTI to BND"]

    def test_multiple_sells_and_one_buy_stay_separate(self):
        trades = [
            trade("Roth", FundType.DOMESTIC_EQUITY, "VTI", "sell", "200.00"),
            trade("Roth", FundType.INTERNATIONAL_EQUITY, "VXUS", "sell", "200.00"),
            trade("Roth", FundType.DOMESTIC_BOND, "BND", "buy", "400.00"),
        ]
        lines = describe_account_trades(trades)
        assert lines == [
            "Sell $200.00 of VTI",
            "Sell $200.00 of VXUS",
            "Buy $400.00 of BND",
        ]

    def test_single_buy_only_stays_a_buy_line(self):
        trades = [trade("Brokerage", FundType.DOMESTIC_EQUITY, "VTI", "buy", "750.00")]
        assert describe_account_trades(trades) == ["Buy $750.00 of VTI"]


class TestGroupTradesByAccount:
    def test_groups_by_account_name(self):
        trades = [
            trade("A", FundType.DOMESTIC_EQUITY, "VTI", "sell", "100"),
            trade("B", FundType.DOMESTIC_EQUITY, "VTI", "buy", "100"),
            trade("A", FundType.DOMESTIC_BOND, "BND", "buy", "100"),
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
            tax_treatment=TaxTreatment.TAX_ADVANTAGED,
            holdings=[
                Holding(fund_type=FundType.DOMESTIC_EQUITY, name="VTI", balance=Decimal(1000)),
                Holding(fund_type=FundType.DOMESTIC_BOND, name="BND", balance=Decimal(0)),
            ],
        )
        target = TargetAllocation(
            domestic_equity_pct=Decimal(50), international_equity_pct=Decimal(0), bond_pct=Decimal(50)
        )
        return account, target

    def test_no_trades_message(self):
        account, target = self.make_account_and_target()
        result = RebalanceResult(trades=[], warnings=[], taxable_bond_dollars=Decimal(0))
        text = format_report([account], target, result)
        assert "already matches your target allocation" in text

    def test_trades_grouped_under_account_header(self):
        account, target = self.make_account_and_target()
        result = RebalanceResult(
            trades=[
                trade("Roth", FundType.DOMESTIC_EQUITY, "VTI", "sell", "500.00"),
                trade("Roth", FundType.DOMESTIC_BOND, "BND", "buy", "500.00"),
            ],
            warnings=[],
            taxable_bond_dollars=Decimal(0),
        )
        text = format_report([account], target, result)
        assert "Roth (Roth IRA):" in text
        assert "Exchange $500.00 from VTI to BND" in text

    def test_warnings_are_included(self):
        account, target = self.make_account_and_target()
        result = RebalanceResult(trades=[], warnings=["Something to flag."], taxable_bond_dollars=Decimal(0))
        text = format_report([account], target, result)
        assert "Warning: Something to flag." in text

    def test_accounts_with_no_trades_are_omitted_from_the_trade_listing(self):
        account_with_trades, target = self.make_account_and_target()
        account_without_trades = Account(
            account_type="Traditional IRA",
            name="Trad IRA",
            tax_treatment=TaxTreatment.TAX_ADVANTAGED,
            holdings=[Holding(fund_type=FundType.DOMESTIC_EQUITY, name="VTI", balance=Decimal(500))],
        )
        result = RebalanceResult(
            trades=[trade("Roth", FundType.DOMESTIC_EQUITY, "VTI", "sell", "500.00")],
            warnings=[],
            taxable_bond_dollars=Decimal(0),
        )
        text = format_report([account_with_trades, account_without_trades], target, result)
        assert "Roth (Roth IRA):" in text
        assert "Trad IRA" not in text

    def test_cash_investment_note_shown_when_present(self):
        account = Account(
            account_type="Taxable Brokerage",
            name="Brokerage",
            tax_treatment=TaxTreatment.TAXABLE,
            holdings=[
                Holding(fund_type=FundType.DOMESTIC_EQUITY, name="VTI", balance=Decimal(0)),
                Holding(fund_type=FundType.CASH, name="", balance=Decimal(1000)),
            ],
        )
        target = TargetAllocation(
            domestic_equity_pct=Decimal(100), international_equity_pct=Decimal(0), bond_pct=Decimal(0)
        )
        result = RebalanceResult(
            trades=[trade("Brokerage", FundType.DOMESTIC_EQUITY, "VTI", "buy", "1000.00")],
            warnings=[],
            taxable_bond_dollars=Decimal(0),
        )
        text = format_report([account], target, result)
        assert "investing $1,000.00 of uninvested cash" in text
