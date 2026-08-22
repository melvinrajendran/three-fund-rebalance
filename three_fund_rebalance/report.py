"""Turns a RebalanceResult into human-readable output.

This is the program's product, not another prompt, so it restates everything
it was given -- the target allocation and where it came from, the rebalancing
band, and the accounts and holdings it was computed against -- before showing
the current-vs-target comparison, the orders to place, and where they land.
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

from three_fund_rebalance.allocation import (
    effective_band_points,
    target_dollar_amounts,
    target_percentages,
)
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

#: Closes every report, because the report is the artifact that gets
#: screenshotted and acted on days later -- a disclaimer that lives only in
#: the README does not travel with it.
#:
#: Two clauses. "Not advice" is the substance; "not a recommendation" is the
#: Reg BI / FINRA 2111 term of art, and disclaiming it is the other half of
#: never using the word anywhere above. A longer draft also disclaimed the
#: advisory relationship, order placement and trademark use -- all true, and
#: all cut, because eight lines of legal prose at the foot of a page is
#: something a reader learns to skip, which costs the disclosure the one
#: thing it is there for. The README's Disclaimer section carries the full
#: set; --version carries the non-affiliation half.
DISCLAIMER = (
    "Not investment, tax, or legal advice, and not a recommendation to buy or sell. "
    "Consult a professional about your situation."
)


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
    # The other half of the band: a share of each class's own target. None
    # when only the absolute half applies -- distinct from 0, which tolerates
    # no drift at all.
    relative_band_pct: Decimal | None = None
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


#: Small counts are spelled out, because the only place one appears starts a
#: sentence -- and "1 order smaller than $1.00 was left out" opens on a
#: numeral, which reads as a fragment rather than a sentence. Nine is where
#: the usual editorial rule stops; past it the figure is both correct and
#: vanishingly unlikely, since it would take ten separate sub-dollar orders.
_COUNT_WORDS = ("", "One", "Two", "Three", "Four", "Five", "Six", "Seven", "Eight", "Nine")


def _count(n: int) -> str:
    return _COUNT_WORDS[n] if 1 <= n < len(_COUNT_WORDS) else str(n)


def summarize_allocation(
    accounts: list[Account],
    target: TargetAllocation,
    band_pct: Decimal = Decimal(0),
    relative_band_pct: Decimal | None = None,
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

    band_points = effective_band_points(target, band_pct, relative_band_pct)

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
                within_band=abs(drift_pct) <= band_points[_CATEGORY_TARGET_KEYS[label]],
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
            # "where stocks are" is not padding: without a subject, "split on
            # VT's 61.9% U.S. allocation" attaches to the whole 95/5 line
            # above it, which says VT decides the bond share too.
            f"From {inputs.stock_pct:.1f}% stocks / {inputs.bond_pct:.1f}% bonds, where "
            f"stocks are split on VT's {inputs.vt.us_pct:.1f}% U.S. allocation "
            f"({inputs.vt.as_of}).",
            indent=INDENT_UNIT,
        )
    )
    return lines


#: How the band behaves once a class leaves it. Stated wherever the band is
#: described, because it is the surprising half: the band decides *whether*
#: to rebalance, not how far. "Traded", not "sold" -- a class outside its
#: band can be under target as easily as over, and correcting it means
#: buying.
_BAND_RULE = (
    "No trades while every asset class is inside its band; once one falls outside, all "
    "three go back to target."
)


def _band_is_on(inputs: RebalanceInputs) -> bool:
    """Zero on either half tolerates no drift at all, which is the same thing
    as having no band."""
    return inputs.band_pct > 0 and inputs.relative_band_pct != 0


def _describe_band_extent(inputs: RebalanceInputs) -> str:
    """Name the band in running prose. Only nameable as one number when the
    absolute half is the whole of it; otherwise each class has its own, and
    the "Rebalancing band" section is where they are written out."""
    if inputs.relative_band_pct is None:
        return f"your band of plus or minus {inputs.band_pct:.1f} percentage points"
    return "its rebalancing band"


def _band_ranges(inputs: RebalanceInputs) -> list[tuple[str, Decimal, Decimal]]:
    """Each class's band as the share of the portfolio it may occupy. The
    two halves of the band are combined per class, so this is the only place
    the resulting numbers can be read off directly."""
    points = effective_band_points(inputs.target, inputs.band_pct, inputs.relative_band_pct)
    target_pcts = target_percentages(inputs.target)
    return [
        (
            label,
            max(Decimal(0), target_pcts[key] - points[key]),
            min(Decimal(100), target_pcts[key] + points[key]),
        )
        for label, key in ((lbl, _CATEGORY_TARGET_KEYS[lbl]) for lbl in _CATEGORY_LABELS)
    ]


def _describe_band(inputs: RebalanceInputs) -> list[str]:
    lines = _subheading("Rebalancing band")
    if inputs.band_pct == 0 or inputs.relative_band_pct == 0:
        lines.append("Off -- every asset class is traded back to its exact target.")
        return lines

    if inputs.relative_band_pct is None:
        lines.append(wrap(f"Plus or minus {inputs.band_pct:.1f} percentage points. {_BAND_RULE}"))
        return lines

    # Two rules meeting at whichever is tighter give each class a different
    # band, so the classes are listed rather than described -- a reader
    # should not have to work out that 25% of a 5% target is 1.2 points.
    lines.append(
        wrap(
            f"Plus or minus {inputs.band_pct:.1f} percentage points, or "
            f"{inputs.relative_band_pct:.1f}% of an asset class's own target, whichever is "
            "tighter:"
        )
    )
    lines.append("")
    ranges = _band_ranges(inputs)
    label_w = max(len(label) for label, _, _ in ranges)
    cells = [(label, f"{low:.1f}%", f"{high:.1f}%") for label, low, high in ranges]
    low_w = max(len(low) for _, low, _ in cells)
    high_w = max(len(high) for _, _, high in cells)
    for label, low, high in cells:
        lines.append(f"{INDENT_UNIT}{label:<{label_w}}  {low:>{low_w}} to {high:>{high_w}}")
    lines.append("")
    lines.append(wrap(_BAND_RULE))
    return lines


def _describe_accounts(inputs: RebalanceInputs) -> list[str]:
    lines = _subheading("Your accounts")
    for account in inputs.accounts:
        # Every account reads `nickname (type, treatment)`, with no exceptions
        # -- one line shaped like the next is what lets the eye compare them
        # down the page. There used to be a rule suppressing the treatment
        # when the type already said it, for the sake of "Taxable Brokerage";
        # since that type became plain "Brokerage" no type names its own
        # treatment, and a branch that can never fire is worse than none.
        #
        # Inside the parentheses rather than after a dash: it is shorter, and
        # a nickname at the cap plus the longest type and treatment still
        # lands well inside the page instead of wrapping with a stranded "--".
        treatment = TAX_TREATMENT_LABELS[account.tax_treatment]
        heading = format_account_heading(account.name, f"{account.account_type}, {treatment}")

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
            wrap('"Tax-free" means qualified withdrawals only; Roth and HSA rules apply.')
        )
    return lines


def _describe_comparison(inputs: RebalanceInputs, summary: AllocationSummary) -> list[str]:
    lines = _subheading("Current vs. target allocation")
    total = f"Total portfolio value: {_money(summary.total_value)}"
    if summary.available_cash > 0:
        total += f" (includes {_money(summary.available_cash)} of cash to invest)"
    lines.append(wrap(total))
    provenance = "Values as entered, not live market prices."
    if inputs.values_as_of:
        provenance += f" Last saved {inputs.values_as_of}."
    lines.append(wrap(provenance, indent=INDENT_UNIT))
    lines.append("")

    banded = _band_is_on(inputs)
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
        lines.append(f"{INDENT_UNIT}* outside {_describe_band_extent(inputs)}")
    return lines


def _describe_outcome(inputs: RebalanceInputs, trades: list[Trade]) -> list[str]:
    """One line saying where the trades land -- the question the rest of the
    report only answers by implication.

    Stated conditionally, because it is arithmetic on the values the user
    typed rather than an outcome anyone can promise: an order fills at the
    market's price on the day, not at the figure entered here. "After these
    trades: 50.0% bonds" reads as a guarantee of a number that will in fact
    be slightly different.
    """
    total = sum((a.total_value() for a in inputs.accounts), Decimal(0))
    if total <= 0:
        return []
    after = allocation_after_trades(inputs.accounts, trades)
    parts = [
        f"{after['U.S. stocks'] / total * 100:.1f}% U.S.",
        f"{after['International stocks'] / total * 100:.1f}% international",
        f"{after['Bonds'] / total * 100:.1f}% bonds",
    ]
    return ["", wrap("If filled at the values you entered: " + " / ".join(parts))]


def _describe_taxable_sales(inputs: RebalanceInputs, trades: list[Trade]) -> list[str]:
    """Disclose that a sale in a taxable account is a taxable event.

    Only when the plan actually sells in one -- a taxable buy realizes
    nothing, and a note that fires either way is one a reader learns to skip.
    Phase 2 minimizes taxable *volume*, which is not the same as pricing the
    sale, so this discloses the event without implying the solver costed it.
    """
    taxable = {a.name for a in inputs.accounts if a.tax_treatment == TaxTreatment.TAXABLE}
    sold = sum(
        (t.amount for t in trades if t.action == "sell" and t.account_name in taxable),
        Decimal(0),
    )
    if sold <= 0:
        return []
    return [
        "",
        wrap(
            f"Selling {_money(sold)} in your taxable accounts may realize capital gains "
            "or losses; no cost basis is collected here, so that tax is not estimated."
        ),
    ]


def format_report(inputs: RebalanceInputs, result: RebalanceResult) -> str:
    summary = summarize_allocation(
        inputs.accounts, inputs.target, inputs.band_pct, inputs.relative_band_pct
    )

    lines = _describe_target(inputs)
    lines.append("")
    lines.extend(_describe_band(inputs))
    lines.append("")
    lines.extend(_describe_accounts(inputs))
    lines.append("")
    lines.extend(_describe_comparison(inputs, summary))
    lines.append("")
    # Not "trades to reach your target": a portfolio inside its band is left
    # where it is, dropped sub-minimum moves stop short, and an account
    # holding a single fund can pin a class out of reach. The outcome line
    # below is what says where the orders actually land.
    lines.extend(_subheading("Orders to place"))

    if not result.trades:
        if _band_is_on(inputs):
            lines.append(
                wrap(f"Every asset class is within {_describe_band_extent(inputs)} -- no trades "
                     "needed.")
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
        lines.extend(_describe_taxable_sales(inputs, result.trades))
        if result.dropped_trades:
            one = result.dropped_trades == 1
            lines.append("")
            lines.append(
                wrap(
                    # "Order", not "move": everything under this heading is an
                    # order not yet placed, so a dropped one is simply an order
                    # missing from the list. A third noun for the same thing is
                    # a vocabulary the reader would have to learn. It forces
                    # "the above orders" at the end, since "these orders" would
                    # then point at either the listed ones or the dropped one.
                    f"{_count(result.dropped_trades)} {'order' if one else 'orders'} "
                    f"smaller than {_money(MIN_TRADE_DOLLARS)} {'was' if one else 'were'} "
                    "left out as impractical, so the above orders do not reach the "
                    "target exactly."
                )
            )

    # Warnings sit with the orders they are about, rather than stranded above
    # the comparison table where they were easy to scroll straight past.
    for warning in result.warnings:
        lines.append("")
        lines.append(wrap(f"Warning: {warning}"))

    lines.append("")
    lines.append(wrap(DISCLAIMER))
    return "\n".join(lines)
