"""Turns a RebalanceResult into human-readable output: a current-vs-target
allocation summary, plus trades grouped by account with buy/sell pairs
within an account collapsed into a single "exchange" line where that reads
more naturally (the common case of moving money from one fund to another
within the same account)."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from three_fund_rebalance.allocation import target_dollar_amounts
from three_fund_rebalance.formatting import (
    INDENT_UNIT,
    format_account_heading,
    format_subheading,
)
from three_fund_rebalance.models import Account, TargetAllocation, Trade, to_cents
from three_fund_rebalance.rebalance import RebalanceResult

_CATEGORY_LABELS = ("Domestic equity", "International equity", "Bonds")
_CATEGORY_TARGET_KEYS = {
    "Domestic equity": "domestic_equity",
    "International equity": "international_equity",
    "Bonds": "bond",
}


@dataclass(frozen=True)
class CategorySummary:
    label: str
    current_amount: Decimal
    current_pct: Decimal
    target_amount: Decimal
    target_pct: Decimal


@dataclass(frozen=True)
class AllocationSummary:
    total_value: Decimal
    uninvested_cash: Decimal
    categories: list[CategorySummary]


def _subheading(text: str) -> list[str]:
    return format_subheading(text).split("\n")


def summarize_allocation(accounts: list[Account], target: TargetAllocation) -> AllocationSummary:
    total = sum((a.total_value() for a in accounts), Decimal(0))
    uninvested_cash = sum((a.cash_balance() for a in accounts), Decimal(0))

    current_by_label = {
        "Domestic equity": sum(
            (h.domestic_equity_component() for a in accounts for h in a.holdings), Decimal(0)
        ),
        "International equity": sum(
            (h.international_equity_component() for a in accounts for h in a.holdings), Decimal(0)
        ),
        "Bonds": sum((h.bond_component() for a in accounts for h in a.holdings), Decimal(0)),
    }
    target_pct_by_label = {
        "Domestic equity": target.domestic_equity_pct,
        "International equity": target.international_equity_pct,
        "Bonds": target.bond_pct,
    }
    target_amounts = (
        target_dollar_amounts(target, total)
        if total > 0
        else {"domestic_equity": Decimal(0), "international_equity": Decimal(0), "bond": Decimal(0)}
    )

    categories = []
    for label in _CATEGORY_LABELS:
        current_amount = current_by_label[label]
        current_pct = (current_amount / total * Decimal(100)) if total > 0 else Decimal(0)
        categories.append(
            CategorySummary(
                label=label,
                current_amount=to_cents(current_amount),
                current_pct=current_pct,
                target_amount=to_cents(target_amounts[_CATEGORY_TARGET_KEYS[label]]),
                target_pct=target_pct_by_label[label],
            )
        )
    return AllocationSummary(
        total_value=to_cents(total), uninvested_cash=to_cents(uninvested_cash), categories=categories
    )


def group_trades_by_account(trades: list[Trade]) -> dict[str, list[Trade]]:
    grouped: dict[str, list[Trade]] = {}
    for trade in trades:
        grouped.setdefault(trade.account_name, []).append(trade)
    return grouped


def describe_account_trades(trades: list[Trade]) -> list[str]:
    """Render one account's trades as display lines. When an account has
    exactly one sell and one buy, it reads more naturally as a single fund
    exchange than as two separate line items; any other combination (e.g.
    two sells funding one buy) is listed as separate buy/sell lines since
    there's no single well-defined pairing."""
    sells = [t for t in trades if t.action == "sell"]
    buys = [t for t in trades if t.action == "buy"]
    if len(sells) == 1 and len(buys) == 1:
        return [f"Exchange ${sells[0].amount:,.2f} from {sells[0].fund_name} to {buys[0].fund_name}"]
    lines = [f"Sell ${t.amount:,.2f} of {t.fund_name}" for t in sells]
    lines += [f"Buy ${t.amount:,.2f} of {t.fund_name}" for t in buys]
    return lines


def format_report(accounts: list[Account], target: TargetAllocation, result: RebalanceResult) -> str:
    summary = summarize_allocation(accounts, target)
    lines = _subheading("Current vs. target allocation")
    lines.append(f"Total portfolio value: ${summary.total_value:,.2f}")
    if summary.uninvested_cash > 0:
        lines.append(
            f"{INDENT_UNIT}(includes ${summary.uninvested_cash:,.2f} of currently uninvested cash)"
        )
    lines.append("")
    for cat in summary.categories:
        lines.append(
            f"{cat.label}: ${cat.current_amount:,.2f} ({cat.current_pct:.1f}%) "
            f"-> target ${cat.target_amount:,.2f} ({cat.target_pct:.1f}%)"
        )

    if result.warnings:
        lines.append("")
        for warning in result.warnings:
            lines.append(f"Warning: {warning}")

    lines.append("")
    if not result.trades:
        lines.append("Your portfolio already matches your target allocation -- no trades needed.")
        return "\n".join(lines)

    lines.extend(_subheading("Recommended trades"))
    lines.append("Place the following orders:")
    grouped = group_trades_by_account(result.trades)
    for account in accounts:
        if account.name not in grouped:
            continue
        # Every account block is preceded by a blank line, the first one
        # included, so the blocks stay uniform -- only the lead-in line sits
        # flush under the subheading. The allocation summary above is likewise
        # flush because it is a single run of lines rather than a series of
        # blocks.
        lines.append("")
        lines.append(INDENT_UNIT + format_account_heading(account.name, account.account_type))
        body_indent = INDENT_UNIT * 2
        cash = account.cash_balance()
        if cash > 0:
            lines.append(
                f"{body_indent}(investing ${cash:,.2f} of uninvested cash as part of these trades)"
            )
        for line in describe_account_trades(grouped[account.name]):
            lines.append(f"{body_indent}{line}")
    return "\n".join(lines)
