"""Turns a RebalanceResult into human-readable output.

This is the program's product, not another prompt, so it restates everything
it was given -- the target allocation and where it came from, the rebalancing
band, and the accounts and holdings it was computed against -- before showing
the current-vs-target comparison, the trades, and where those trades land.
Read on its own, with no scrollback, it should still say what was asked for,
what to do, and what the result will be.

Widths come from `formatting`: prose wraps to `prose_width()`, which follows
the terminal up to a readable maximum, while tables are sized to their own
contents within `table_width()`, which follows the terminal without a cap.
The two are separate because they want opposite things -- a paragraph gets
harder to read as it gets wider, and a table of dollar figures does not.
Amounts are right-aligned in their columns, because the whole point of
putting them in rows is to compare them down the page.

Every percentage here is shown to exactly one decimal place, and a distance
between two percentages is called "percentage points" -- abbreviated "pts"
only in the comparison table's header, where the column cannot take the words.
Prompts do the opposite and trim trailing zeros; see formatting.format_percent.

Trades are grouped by account, with a buy/sell pair within one account
collapsed into a single "exchange" line where that reads more naturally (the
common case of moving money from one fund to another in the same account).
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from decimal import Decimal

from three_fund_rebalance.allocation import target_dollar_amounts
from three_fund_rebalance.config import MIN_TRADE_DOLLARS
from three_fund_rebalance.formatting import (
    ASSET_CLASS_LABELS,
    INDENT_UNIT,
    TAX_TREATMENT_LABELS,
    format_account_heading,
    format_subheading,
    wrap,
)
from three_fund_rebalance.models import (
    Account,
    FundType,
    Holding,
    TargetAllocation,
    TaxTreatment,
    Trade,
    to_cents,
)
from three_fund_rebalance.rebalance import RebalanceResult
from three_fund_rebalance.vt_allocation import VTAllocationResult

_CATEGORY_LABELS = ("U.S. stocks", "International stocks", "Bonds")
_CATEGORY_TARGET_KEYS = {
    "U.S. stocks": "us_stock",
    "International stocks": "international_stock",
    "Bonds": "bond",
}
_COMPONENT_GETTERS = {
    "U.S. stocks": Holding.us_stock_component,
    "International stocks": Holding.international_stock_component,
    "Bonds": Holding.bond_component,
}


@dataclass(frozen=True)
class RebalanceInputs:
    """Everything the user was asked for, carried to the report in one piece
    so recapping an input doesn't mean growing format_report's signature
    every time the flow gains a question."""

    stock_pct: Decimal
    bond_pct: Decimal
    vt: VTAllocationResult
    target: TargetAllocation
    band_pct: Decimal
    accounts: list[Account]
    # When the saved values were last written, if they came from a config
    # file. None when everything was typed this session.
    values_as_of: str | None = None


@dataclass(frozen=True)
class CategorySummary:
    label: str
    current_amount: Decimal
    current_pct: Decimal
    target_amount: Decimal
    target_pct: Decimal
    # Signed percentage points away from target, and whether that is small
    # enough to leave alone. With a band of 0 every nonzero drift is out.
    drift_pct: Decimal
    within_band: bool


@dataclass(frozen=True)
class AllocationSummary:
    total_value: Decimal
    available_cash: Decimal
    categories: list[CategorySummary]


def _subheading(text: str) -> list[str]:
    return format_subheading(text).split("\n")


def _money(amount: Decimal) -> str:
    return f"${amount:,.2f}"


def summarize_allocation(
    accounts: list[Account], target: TargetAllocation, band_pct: Decimal = Decimal(0)
) -> AllocationSummary:
    total = sum((a.total_value() for a in accounts), Decimal(0))
    available_cash = sum((a.available_cash() for a in accounts), Decimal(0))
    holdings = [h for account in accounts for h in account.holdings]

    current_by_label = {
        label: sum((getter(h) for h in holdings), Decimal(0))
        for label, getter in _COMPONENT_GETTERS.items()
    }
    target_pct_by_label = {
        "U.S. stocks": target.us_stock_pct,
        "International stocks": target.international_stock_pct,
        "Bonds": target.bond_pct,
    }
    target_amounts = (
        target_dollar_amounts(target, total)
        if total > 0
        else {"us_stock": Decimal(0), "international_stock": Decimal(0), "bond": Decimal(0)}
    )

    categories = []
    for label in _CATEGORY_LABELS:
        current_amount = current_by_label[label]
        current_pct = (current_amount / total * Decimal(100)) if total > 0 else Decimal(0)
        drift_pct = current_pct - target_pct_by_label[label]
        categories.append(
            CategorySummary(
                label=label,
                current_amount=to_cents(current_amount),
                current_pct=current_pct,
                target_amount=to_cents(target_amounts[_CATEGORY_TARGET_KEYS[label]]),
                target_pct=target_pct_by_label[label],
                drift_pct=drift_pct,
                within_band=abs(drift_pct) <= band_pct,
            )
        )
    return AllocationSummary(
        total_value=to_cents(total), available_cash=to_cents(available_cash), categories=categories
    )


def group_trades_by_account(trades: list[Trade]) -> dict[str, list[Trade]]:
    grouped: dict[str, list[Trade]] = {}
    for trade in trades:
        grouped.setdefault(trade.account_name, []).append(trade)
    return grouped


def describe_account_trades(trades: list[Trade]) -> list[str]:
    """Render one account's trades as display lines. One sell against one buy
    of the *same amount* reads more naturally as a single fund exchange than
    as two line items; anything else is listed as separate buy/sell lines,
    since there is no single well-defined pairing.

    The amounts have to match. An exchange moves one figure out of one fund
    and into another, so collapsing unequal legs states the wrong number for
    one of them: an account with cash to invest sells $16,937 and buys
    $17,437, and "Exchange $16,937.00 from VTI to BND" would have the user
    under-buy by exactly the cash they were told to put to work.
    """
    sells = [t for t in trades if t.action == "sell"]
    buys = [t for t in trades if t.action == "buy"]
    if len(sells) == 1 and len(buys) == 1 and sells[0].amount == buys[0].amount:
        return [
            f"Exchange {_money(sells[0].amount)} from {sells[0].fund_name} to {buys[0].fund_name}"
        ]
    lines = [f"Sell {_money(t.amount)} of {t.fund_name}" for t in sells]
    lines += [f"Buy {_money(t.amount)} of {t.fund_name}" for t in buys]
    return lines


def allocation_after_trades(accounts: list[Account], trades: list[Trade]) -> dict[str, Decimal]:
    """What each asset class will be worth once these orders are filled.

    Applied to the holdings rather than to the class totals, so a trade in a
    target-date fund moves all three of its sleeves by the right fractions.
    Cash is left out: it has no trade of its own because it is not a
    security, but the per-account budget spends every dollar of it, so the
    buys above already account for it.
    """
    deltas: dict[tuple[str, FundType, str], Decimal] = {}
    for trade in trades:
        key = (trade.account_name, trade.fund_type, trade.fund_name)
        signed = trade.amount if trade.action == "buy" else -trade.amount
        deltas[key] = deltas.get(key, Decimal(0)) + signed

    after = []
    for account in accounts:
        for holding in account.holdings:
            if holding.fund_type == FundType.CASH:
                continue
            delta = deltas.get((account.name, holding.fund_type, holding.name), Decimal(0))
            after.append(replace(holding, value=holding.value + delta))

    return {
        label: sum((getter(h) for h in after), Decimal(0))
        for label, getter in _COMPONENT_GETTERS.items()
    }


def _describe_target(inputs: RebalanceInputs) -> list[str]:
    target = inputs.target
    width = max(len(label) for label in _CATEGORY_LABELS)
    lines = _subheading("Target asset allocation")
    percentages = (target.us_stock_pct, target.international_stock_pct, target.bond_pct)
    for label, pct in zip(_CATEGORY_LABELS, percentages):
        lines.append(f"{INDENT_UNIT}{label:<{width}}  {pct:>5.1f}%")
    lines.append("")
    lines.append(
        wrap(
            f"From {inputs.stock_pct:.1f}% stocks / {inputs.bond_pct:.1f}% bonds, with the "
            f"stock side split by VT's {inputs.vt.us_pct:.1f}% U.S. allocation "
            f"({inputs.vt.as_of}).",
            indent=INDENT_UNIT,
        )
    )
    return lines


def _describe_band(inputs: RebalanceInputs) -> list[str]:
    lines = _subheading("Rebalancing band")
    if inputs.band_pct == 0:
        lines.append("Off -- every asset class is traded back to its exact target.")
        return lines
    lines.append(
        wrap(
            # "traded", not "sold": a class inside its band can be under
            # target as easily as over, and correcting it would mean buying.
            # It also matches the band-off line directly above.
            f"Plus or minus {inputs.band_pct:.1f} percentage points. An asset class inside "
            "its band is left alone rather than traded back to the exact target."
        )
    )
    return lines


def _describe_accounts(inputs: RebalanceInputs) -> list[str]:
    lines = _subheading("Your accounts")
    for account in inputs.accounts:
        # The account type usually says how it is taxed already -- "Taxable
        # Brokerage" is taxable -- so name the treatment only when it doesn't.
        # Inside the parentheses rather than after a dash: it is shorter, and
        # a nickname at the cap plus the longest type and treatment still
        # lands at 77 characters instead of wrapping with a stranded "--".
        descriptor = account.account_type
        treatment = TAX_TREATMENT_LABELS[account.tax_treatment]
        if treatment not in account.account_type.lower():
            descriptor = f"{account.account_type}, {treatment}"
        heading = format_account_heading(account.name, descriptor)

        rows = []
        for holding in account.holdings:
            if holding.fund_type == FundType.CASH:
                rows.append(("Cash available to invest", _money(holding.value)))
                continue
            label = ASSET_CLASS_LABELS[holding.fund_type]
            # A declared position holding nothing is capacity, not a holding;
            # "$0.00" gives it a false air of precision.
            amount = _money(holding.value) if holding.value > 0 else "--"
            rows.append((f"{holding.name} ({label} fund)", amount))
        rows.append(("Total", _money(account.total_value())))

        label_width = max(len(label) for label, _ in rows)
        amount_width = max(len(amount) for _, amount in rows)
        body_indent = INDENT_UNIT * 2

        lines.append("")
        lines.append(wrap(heading, indent=INDENT_UNIT, hanging_indent=INDENT_UNIT * 2))
        for label, amount in rows:
            lines.append(f"{body_indent}{label:<{label_width}}  {amount:>{amount_width}}")

    # Roth and HSA withdrawals are tax-free only when qualified. The label is
    # standard shorthand and the column is tight, so it is qualified here.
    if any(a.tax_treatment == TaxTreatment.TAX_FREE for a in inputs.accounts):
        lines.append("")
        lines.append(
            wrap('"Tax-free" means qualified withdrawals; Roth and HSA rules apply.')
        )
    return lines


def _describe_comparison(inputs: RebalanceInputs, summary: AllocationSummary) -> list[str]:
    lines = _subheading("Current vs. target allocation")
    total = f"Total portfolio value: {_money(summary.total_value)}"
    if summary.available_cash > 0:
        total += f" (includes {_money(summary.available_cash)} of cash to invest)"
    lines.append(wrap(total))
    provenance = "Values as entered"
    if inputs.values_as_of:
        provenance += f"; last saved {inputs.values_as_of}"
    lines.append(wrap(f"{provenance}.", indent=INDENT_UNIT))
    lines.append("")

    banded = inputs.band_pct > 0
    drift_header = "Drift (pts)"
    rows = [
        (
            cat.label,
            f"{_money(cat.current_amount)} ({cat.current_pct:.1f}%)",
            f"{_money(cat.target_amount)} ({cat.target_pct:.1f}%)",
            f"{cat.drift_pct:+.1f}",
            "" if not banded or cat.within_band else " *",
        )
        for cat in summary.categories
    ]

    label_w = max(len(row[0]) for row in rows)
    current_w = max(len(row[1]) for row in rows + [("", "Current", "", "", "")])
    target_w = max(len(row[2]) for row in rows + [("", "", "Target", "", "")])
    drift_w = max(len(row[3]) for row in rows + [("", "", "", drift_header, "")])

    lines.append(
        f"{INDENT_UNIT}{'':<{label_w}}  {'Current':>{current_w}}  "
        f"{'Target':>{target_w}}  {drift_header:>{drift_w}}"
    )
    for label, current, target, drift, marker in rows:
        lines.append(
            f"{INDENT_UNIT}{label:<{label_w}}  {current:>{current_w}}  "
            f"{target:>{target_w}}  {drift:>{drift_w}}{marker}"
        )
    if any(row[4] for row in rows):
        lines.append("")
        lines.append(
            f"{INDENT_UNIT}* outside your band of plus or minus "
            f"{inputs.band_pct:.1f} percentage points"
        )
    return lines


def _describe_outcome(inputs: RebalanceInputs, trades: list[Trade]) -> list[str]:
    """One line saying where the trades land -- the question the rest of the
    report only answers by implication."""
    total = sum((a.total_value() for a in inputs.accounts), Decimal(0))
    if total <= 0:
        return []
    after = allocation_after_trades(inputs.accounts, trades)
    parts = [
        f"{after['U.S. stocks'] / total * 100:.1f}% U.S.",
        f"{after['International stocks'] / total * 100:.1f}% international",
        f"{after['Bonds'] / total * 100:.1f}% bonds",
    ]
    return ["", wrap("After these trades: " + " / ".join(parts))]


def format_report(inputs: RebalanceInputs, result: RebalanceResult) -> str:
    summary = summarize_allocation(inputs.accounts, inputs.target, inputs.band_pct)

    lines = _describe_target(inputs)
    lines.append("")
    lines.extend(_describe_band(inputs))
    lines.append("")
    lines.extend(_describe_accounts(inputs))
    lines.append("")
    lines.extend(_describe_comparison(inputs, summary))
    lines.append("")
    # Not "trades to reach your target": with a band the orders stop at the
    # band edge, which the outcome line below then plainly contradicts.
    lines.extend(_subheading("Orders to place"))

    if not result.trades:
        if inputs.band_pct > 0:
            lines.append(
                wrap(
                    "Every asset class is within your band of plus or minus "
                    f"{inputs.band_pct:.1f} percentage points -- no trades needed."
                )
            )
        else:
            lines.append(
                wrap("Your portfolio already matches your target allocation -- no trades needed.")
            )
    else:
        lines.append("Review each order before placing it:")
        grouped = group_trades_by_account(result.trades)
        for account in inputs.accounts:
            if account.name not in grouped:
                continue
            # Every account block is preceded by a blank line, the first one
            # included, so the blocks stay uniform -- only the lead-in line
            # sits flush under the subheading.
            lines.append("")
            lines.append(
                wrap(
                    format_account_heading(account.name, account.account_type),
                    indent=INDENT_UNIT,
                    hanging_indent=INDENT_UNIT * 2,
                )
            )
            body_indent = INDENT_UNIT * 2
            cash = account.available_cash()
            if cash > 0:
                lines.append(f"{body_indent}(includes investing {_money(cash)} of available cash)")
            # An order naming a fund in full rather than by ticker is prose,
            # not a column, so it wraps -- with the continuation set in, so
            # the run of orders still reads as a list.
            for line in describe_account_trades(grouped[account.name]):
                lines.append(
                    wrap(line, indent=body_indent, hanging_indent=body_indent + INDENT_UNIT)
                )
        lines.extend(_describe_outcome(inputs, result.trades))
        if result.dropped_trades:
            noun = "move" if result.dropped_trades == 1 else "moves"
            lines.append("")
            lines.append(
                wrap(
                    f"{result.dropped_trades} {noun} smaller than "
                    f"{_money(MIN_TRADE_DOLLARS)} were left out as impractical, so these "
                    "orders do not reach the target exactly."
                )
            )

    # Warnings sit with the orders they are about, rather than stranded above
    # the comparison table where they were easy to scroll straight past.
    for warning in result.warnings:
        lines.append("")
        lines.append(wrap(f"Warning: {warning}"))

    lines.append("")
    lines.append(
        wrap(
            "Not investment or tax advice. This is a calculation from the accounts and "
            "values you entered; consult a tax or investment professional about your "
            "situation."
        )
    )
    return "\n".join(lines)
