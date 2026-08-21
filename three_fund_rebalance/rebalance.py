"""The core rebalancing engine.

Formulates the rebalance as a small linear program with one decision
variable per (account, holding) "slot" that currently exists, and solves it
in three sequential phases (lexicographic optimization -- each phase's
optimal objective value is carried forward as a "no worse than this" bound
for the next phase, so later phases can only refine, never undo, an earlier
phase's priority):

  Phase 1 -- minimize the total $ of bonds left sitting in taxable accounts.
    This fills tax-advantaged bond capacity first; taxable accounts only end
    up holding bonds once tax-advantaged room is exhausted.

  Phase 2 -- minimize $ trade volume *within taxable accounts*, subject to
    not giving up any of phase 1's bond-minimization result. This is the
    proxy for "avoid triggering capital gains tax": we don't have cost-basis
    data, so we approximate "minimize gains" as "minimize taxable trading".

  Phase 3 -- tie-break by minimizing total $ trade volume across *all*
    accounts (subject to not giving up phases 1 or 2). Phases 1 and 2 alone
    can have multiple equally-good solutions (e.g. which of several
    tax-advantaged accounts absorbs a shift); phase 3 picks the one that
    disturbs the fewest existing positions, which reads as the "nicest"
    recommendation.

Each account's total value is treated as fixed -- a rebalance only moves
money between funds *within* an account (including investing any cash
sitting there); it never moves money between accounts.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from scipy.optimize import linprog

from three_fund_rebalance.allocation import target_dollar_amounts
from three_fund_rebalance.config import MIN_TRADE_DOLLARS
from three_fund_rebalance.formatting import ASSET_CLASS_LABELS
from three_fund_rebalance.models import (
    CENT,
    Account,
    FundType,
    Holding,
    TargetAllocation,
    TaxTreatment,
    Trade,
    to_cents,
)

# Slack allowed when carrying one phase's optimal objective value forward as
# a "<=" bound for the next phase. HiGHS (scipy's default LP solver) is not
# bit-exact, so a hard "<=" against the raw optimum can spuriously reject the
# true optimum of the next phase over floating point noise.
_OBJECTIVE_SLACK = 0.01

# Fund types whose dollar target we're solving for (CASH is excluded: it is
# not a security, and its target is implicitly zero -- see _build_slots).
_TARGET_FUND_TYPES = (FundType.US_STOCK, FundType.INTERNATIONAL_STOCK, FundType.US_BOND)
_TARGET_KEYS = {
    FundType.US_STOCK: "us_stock",
    FundType.INTERNATIONAL_STOCK: "international_stock",
    FundType.US_BOND: "bond",
}


class RebalanceError(Exception):
    """Raised when no feasible rebalance exists for the given accounts and
    target -- e.g. the target requires more bond capacity than any
    combination of declared holdings can provide."""


@dataclass(frozen=True)
class _Slot:
    """One decision variable: how much to hold in `holding` within the
    account at `account_index`."""

    account_index: int
    holding: Holding

    @property
    def fund_type(self) -> FundType:
        return self.holding.fund_type


@dataclass
class RebalanceResult:
    trades: list[Trade]
    warnings: list[str]
    # Total $ of bonds left in taxable accounts in the final solution (0 if
    # tax-advantaged capacity was sufficient to hold the whole bond target).
    taxable_bond_dollars: Decimal


def _to_decimal(value: float) -> Decimal:
    # Route through str() to avoid dragging in float's binary-fraction noise
    # (Decimal(0.1) != Decimal("0.1")); six decimal places is far finer than
    # the cent precision we ultimately round to.
    return Decimal(str(round(value, 6)))


def _fund_type_coefficient(slot: _Slot, target_type: FundType) -> float:
    """How much of slot's dollar value counts toward `target_type` (1.0 for
    a direct match, the fund's internal % for a target-date slot, 0.0
    otherwise)."""
    if slot.fund_type == target_type:
        return 1.0
    if slot.fund_type == FundType.TARGET_DATE:
        pct = {
            FundType.US_STOCK: slot.holding.target_date_allocation.us_stock_pct,
            FundType.INTERNATIONAL_STOCK: slot.holding.target_date_allocation.international_stock_pct,
            FundType.US_BOND: slot.holding.target_date_allocation.bond_pct,
        }[target_type]
        return float(pct) / 100.0
    return 0.0


def _build_slots(accounts: list[Account]) -> list[_Slot]:
    slots = []
    for account_index, account in enumerate(accounts):
        tradeable = [h for h in account.holdings if h.fund_type != FundType.CASH]
        if not tradeable and account.total_value() > 0:
            raise RebalanceError(
                f"Account '{account.name}' has ${account.total_value():,.2f} but no fund holdings "
                "declared to invest it in -- add at least one fund holding for this account."
            )
        slots.extend(_Slot(account_index=account_index, holding=h) for h in tradeable)
    return slots


def _check_names_unique(accounts: list[Account]) -> None:
    names = [a.name for a in accounts]
    duplicates = {name for name in names if names.count(name) > 1}
    if duplicates:
        listed = ", ".join(repr(name) for name in sorted(duplicates))
        raise RebalanceError(f"Account names must be unique; duplicated: {listed}")


def _check_capacity_feasible(
    slots: list[_Slot], bounds: list[tuple[float, float]], dollar_targets: dict[str, Decimal]
) -> None:
    """Fail fast with a specific, actionable message when the target simply
    cannot be reached given which fund types are declared where -- rather
    than surfacing scipy's generic 'infeasible' message."""
    for fund_type in _TARGET_FUND_TYPES:
        max_capacity = sum(
            _fund_type_coefficient(slot, fund_type) * upper for slot, (_, upper) in zip(slots, bounds)
        )
        target = float(dollar_targets[_TARGET_KEYS[fund_type]])
        if target > max_capacity + 0.01:
            raise RebalanceError(
                f"Target {ASSET_CLASS_LABELS[fund_type]} allocation is "
                f"${target:,.2f}, but no combination of the funds you listed can hold "
                f"more than ${max_capacity:,.2f} -- add a matching fund (or a target-date "
                "fund) to an account to make room."
            )


def _distribute_residual(
    values: dict[int, Decimal], raw_values: list[Decimal], residual: Decimal
) -> None:
    """Apply `residual` to `values` (in place) one cent at a time, using the
    largest-remainder method: each cent goes to whichever slot rounding moved
    furthest in the opposite direction, so it lands where it distorts least."""
    if residual == 0:
        return
    step = CENT if residual > 0 else -CENT
    ordered = sorted(values, key=lambda i: raw_values[i] - values[i], reverse=residual > 0)
    cents_remaining = int((abs(residual) * 100).to_integral_value())

    # Bounded loop: a slot clamped at zero is skipped, so allow enough passes
    # to cycle past clamped slots without ever spinning forever.
    for position in range(cents_remaining * len(ordered) + len(ordered)):
        if cents_remaining == 0:
            break
        i = ordered[position % len(ordered)]
        candidate = values[i] + step
        if candidate >= 0:  # never drive a holding negative
            values[i] = candidate
            cents_remaining -= 1


def _finalize_account_values(
    account: Account, indices: list[int], slots: list[_Slot], raw_values: list[Decimal]
) -> dict[int, Decimal]:
    """Turn one account's solved (fractional) slot values into final cent
    amounts that both round cleanly and leave the account summing to exactly
    its own total.

    Two things have to hold together here, and doing them in sequence breaks
    them: rounding each slot independently can leave an account a cent off
    its real value, and dropping a sub-minimum trade afterwards reopens the
    same gap from the other side. Together those produced recommendations
    like "buy $5,000.01" against exactly $5,000.00 of cash -- the offsetting
    $0.01 sell was filtered out as too small while the extra cent of buying
    survived.

    So slots that end up trading less than the minimum are snapped back to
    their current value and the freed money is redistributed among the
    slots that are still trading, repeating until nothing new falls below
    the minimum. Preserving the per-account total is the hard constraint --
    you cannot spend money you don't have -- while the aggregate allocation
    targets are approximate goals, so any leftover cent is absorbed there.
    """
    target_total = to_cents(account.total_value())
    current = {i: to_cents(slots[i].holding.value) for i in indices}
    held: set[int] = set()  # slots pinned at their current value (no trade)

    # Each pass pins at least one more slot, so this runs at most once per slot.
    for _ in range(len(indices) + 1):
        tradeable = [i for i in indices if i not in held]
        if not tradeable:
            return current

        budget = target_total - sum((current[i] for i in held), Decimal(0))
        values = {i: to_cents(raw_values[i]) for i in tradeable}
        _distribute_residual(values, raw_values, budget - sum(values.values()))

        too_small = [i for i in tradeable if abs(values[i] - current[i]) < MIN_TRADE_DOLLARS]
        if not too_small:
            return {**{i: current[i] for i in held}, **values}
        held.update(too_small)

    # Unreachable: every pass either returns or pins at least one more slot,
    # so the loop runs out of tradeable slots (and returns above) first. Kept
    # as a backstop so a future change can't fall through to an implicit None.
    return current  # pragma: no cover


def _solve(c, A_eq, b_eq, A_ub, b_ub, bounds, context: str):
    result = linprog(c=c, A_eq=A_eq, b_eq=b_eq, A_ub=A_ub, b_ub=b_ub, bounds=bounds, method="highs")
    if not result.success:
        raise RebalanceError(f"Could not find a feasible rebalance ({context}): {result.message}")
    return result


def compute_trades(accounts: list[Account], target: TargetAllocation) -> RebalanceResult:
    _check_names_unique(accounts)

    total_value = sum((a.total_value() for a in accounts), Decimal(0))
    if total_value <= 0:
        return RebalanceResult(trades=[], warnings=[], taxable_bond_dollars=Decimal(0))

    dollar_targets = target_dollar_amounts(target, total_value)
    slots = _build_slots(accounts)
    # n cannot be 0 here: total_value > 0 (checked above) means at least one
    # account has a positive value, and _build_slots already rejects any
    # positive-value account that declares no tradeable holdings.
    n = len(slots)

    current = [float(slot.holding.value) for slot in slots]
    bounds = [(0.0, float(accounts[s.account_index].total_value())) for s in slots]

    # --- equality constraints shared by all three phases -----------------
    # One row per account: that account's slots must sum to its fixed total.
    budget_rows, budget_rhs = [], []
    for account_index, account in enumerate(accounts):
        indices = [i for i, s in enumerate(slots) if s.account_index == account_index]
        if not indices:
            continue
        row = [0.0] * n
        for i in indices:
            row[i] = 1.0
        budget_rows.append(row)
        budget_rhs.append(float(account.total_value()))

    # Three rows: aggregate U.S. stock / international stock / bond dollar targets.
    aggregate_rows = [
        [_fund_type_coefficient(slot, fund_type) for slot in slots] for fund_type in _TARGET_FUND_TYPES
    ]
    aggregate_rhs = [float(dollar_targets[_TARGET_KEYS[ft]]) for ft in _TARGET_FUND_TYPES]

    _check_capacity_feasible(slots, bounds, dollar_targets)

    A_eq = budget_rows + aggregate_rows
    b_eq = budget_rhs + aggregate_rhs

    # --- phase 1: minimize $ of bonds left in taxable accounts -----------
    c1 = [
        _fund_type_coefficient(slot, FundType.US_BOND)
        if accounts[slot.account_index].tax_treatment == TaxTreatment.TAXABLE
        else 0.0
        for slot in slots
    ]
    phase1 = _solve(c1, A_eq, b_eq, None, None, bounds, "phase 1: minimizing taxable bonds")

    # --- phases 2 & 3 extend the variable vector with y_i = |x_i - current_i| ---
    # Standard LP linearization of absolute value: minimizing y_i subject to
    # y_i >= x_i - current_i and y_i >= current_i - x_i forces y_i to exactly
    # |x_i - current_i| at the optimum (since the objective always wants y_i
    # as small as the constraints allow).
    def pad(row):
        return list(row) + [0.0] * n

    A_eq2 = [pad(row) for row in A_eq]
    b_eq2 = list(b_eq)
    bounds2 = bounds + [(0.0, None)] * n

    A_ub2, b_ub2 = [], []
    for i in range(n):
        row = [0.0] * (2 * n)
        row[i], row[n + i] = 1.0, -1.0
        A_ub2.append(row)  # x_i - y_i <= current_i
        b_ub2.append(current[i])

        row = [0.0] * (2 * n)
        row[i], row[n + i] = -1.0, -1.0
        A_ub2.append(row)  # -x_i - y_i <= -current_i
        b_ub2.append(-current[i])

    # Carry phase 1's optimum forward: don't allow taxable bonds to regress.
    A_ub2.append(pad(c1))
    b_ub2.append(phase1.fun + _OBJECTIVE_SLACK)

    taxable_mask = [accounts[s.account_index].tax_treatment == TaxTreatment.TAXABLE for s in slots]

    # --- phase 2: minimize $ trade volume within taxable accounts --------
    c2 = [0.0] * n + [1.0 if taxable else 0.0 for taxable in taxable_mask]
    phase2 = _solve(
        c2, A_eq2, b_eq2, A_ub2, b_ub2, bounds2, "phase 2: minimizing taxable trade volume"
    )

    A_ub3 = A_ub2 + [c2]
    b_ub3 = b_ub2 + [phase2.fun + _OBJECTIVE_SLACK]

    # --- phase 3: tie-break by minimizing total trade volume everywhere --
    c3 = [0.0] * n + [1.0] * n
    phase3 = _solve(
        c3, A_eq2, b_eq2, A_ub3, b_ub3, bounds2, "phase 3: minimizing total trade volume"
    )

    raw_values = [_to_decimal(v) for v in phase3.x[:n]]
    new_values = [Decimal(0)] * n
    for account_index, account in enumerate(accounts):
        indices = [i for i, s in enumerate(slots) if s.account_index == account_index]
        if not indices:
            continue
        for i, value in _finalize_account_values(account, indices, slots, raw_values).items():
            new_values[i] = value

    # _finalize_account_values already snapped every sub-minimum move back to
    # its current value, so any remaining delta is a real, fillable trade.
    trades = []
    for slot, new_value in zip(slots, new_values):
        delta = new_value - slot.holding.value
        if delta == 0:
            continue
        trades.append(
            Trade(
                account_name=accounts[slot.account_index].name,
                fund_type=slot.fund_type,
                fund_name=slot.holding.name,
                action="buy" if delta > 0 else "sell",
                amount=abs(delta),
            )
        )
    trades.sort(key=lambda t: (t.account_name, t.action == "buy", t.fund_type.value, t.fund_name))

    taxable_bond_dollars = Decimal(0)
    for slot, new_value in zip(slots, new_values):
        if accounts[slot.account_index].tax_treatment != TaxTreatment.TAXABLE:
            continue
        taxable_bond_dollars += new_value * Decimal(str(_fund_type_coefficient(slot, FundType.US_BOND)))
    taxable_bond_dollars = to_cents(taxable_bond_dollars)

    warnings = []
    if taxable_bond_dollars > 0:
        warnings.append(
            "Your tax-advantaged accounts don't have enough room for the full bond target; "
            f"${taxable_bond_dollars:,} in bonds will remain in taxable accounts "
            "(minimized as much as possible)."
        )

    return RebalanceResult(trades=trades, warnings=warnings, taxable_bond_dollars=taxable_bond_dollars)
