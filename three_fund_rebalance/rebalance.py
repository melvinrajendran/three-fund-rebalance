"""The core rebalancing engine.

Formulates the rebalance as a small linear program and solves it in six
sequential phases (lexicographic optimization -- each phase's optimal
objective value is carried forward as a "no worse than this" bound for the
next phase, so later phases can only refine, never undo, an earlier phase's
priority):

  Phase 1 -- minimize the total $ of bonds left sitting in taxable accounts.
    This fills sheltered bond capacity first; taxable accounts only end up
    holding bonds once tax-advantaged room is exhausted.

  Phase 2 -- minimize $ trade volume *within taxable accounts*, subject to
    not giving up any of phase 1's bond-minimization result. This is the
    proxy for "avoid triggering capital gains tax": we don't have cost-basis
    data, so we approximate "minimize gains" as "minimize taxable trading".

    Worth knowing what this is equivalent to. Each account's total is fixed
    and its cash is always fully invested, so within one taxable account
    buys minus sells is a constant; minimizing buys plus sells is therefore
    the same thing as minimizing dollars *sold*. Investing available cash
    costs nothing under this objective, which is what makes new money the
    first thing spent.

  Phase 3 -- minimize wash-sale exposure: the $ of any one fund sold in a
    taxable account while the same fund is bought in a tax-advantaged one.
    Selling at a loss and buying a substantially identical security within
    30 days either way is a wash sale, and when the replacement lands inside
    an IRA or 401(k) the disallowed loss is *not* added back to basis the
    way an ordinary wash sale's is -- it is gone for good (Rev. Rul. 2008-5).
    Ranked above both placement phases below because a permanently destroyed
    loss costs more than either placement is worth, and below phase 2 so it
    never opens a taxable trade of its own.

  Phase 4 -- minimize the $ of bonds held in *tax-free* accounts, i.e. hold
    them in tax-deferred space instead. Both shelter the interest from tax
    today, but a Roth or HSA never taxes qualified withdrawals, so its space
    is worth the most held against the highest expected return -- stocks.
    Bonds belong in the account that will be taxed as ordinary income on the
    way out regardless.

    Unlike phase 5 below, this counts a target-date fund's bond sleeve via
    _fund_type_coefficient. Bonds inside a Roth's target-date fund really
    are bonds occupying tax-free space, exactly as phase 1 counts them, and
    such an account is pinned by its own budget row anyway -- so counting
    them states the truth without giving the solver anything to act on.

  Phase 5 -- minimize the $ of the international fund held in tax-advantaged
    accounts, i.e. prefer it in taxable. A fund that is majority-foreign can
    pass the foreign tax withheld on its holdings through to you as a credit
    you can claim; held in an IRA or 401(k), that credit is simply lost.

    Ranked deliberately *below* phase 2, not above it. The credit is worth a
    couple of basis points a year, while selling appreciated stock in a
    taxable account to chase it can realize a far larger gain today -- and we
    have no cost-basis data, so we cannot even see that trade-off. Sitting
    under the taxable-trading objective makes this a tie-break: it decides
    which funds an account already being traded ends up holding -- the sell
    side as much as the buy side -- and never starts a taxable trade of its
    own.

    Only a dedicated international fund counts. A target-date fund is not
    majority-foreign, so it cannot pass the credit through from either kind
    of account, and there is nothing to be gained by shuffling it around --
    hence the direct fund-type test below rather than _fund_type_coefficient.

  Phase 6 -- tie-break by minimizing total $ trade volume across *all*
    accounts (subject to not giving up phases 1-5). The earlier phases alone
    can have multiple equally-good solutions (e.g. which of several
    tax-advantaged accounts absorbs a shift); phase 6 picks the one that
    disturbs the fewest existing positions, which reads as the "nicest"
    plan.

All six run *after* `_resolve_allocation` has settled what each asset class
should be worth, and against that as a hard equality. The order matters:
**what to hold is decided before where to hold it, and never by it.**

That is what the rebalancing band buys. Trading to an exact target means
every drift, however small, generates trades, and in a taxable account those
cost real money to correct a rounding error; inside the band the allocation
is simply left where it is. But a band stated as a *range* the six phases
below could see would be a range they could spend: every one of them is
phrased as "minimize this asset class in that kind of account", which only
means "relocate it" while the class total is fixed. Given slack, they will
satisfy themselves by holding less of the asset class instead of moving it.
Deciding the totals up front leaves the phases exactly the freedom they were
designed for -- moving a holding between accounts never changes an asset
class total -- and none of the freedom they were not.

Each account's total value is treated as fixed -- a rebalance only moves
money between funds *within* an account (including investing any cash
sitting there); it never moves money between accounts.

One consequence of an account holding either a target-date fund or
individual funds (never both): a target-date account has exactly one slot,
so the per-account budget constraint pins it outright. Its only possible
"trade" is investing its own cash into the fund it already holds, and no
objective can reach it. That is what keeps the solver from ever proposing
to liquidate a target-date fund to relocate the bond sleeve inside it.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from decimal import Decimal

from scipy.optimize import linprog

from three_fund_rebalance.allocation import (
    ASSET_CLASS_KEYS,
    target_dollar_amounts,
    target_dollar_bounds,
)
from three_fund_rebalance.config import MIN_TRADE_DOLLARS
from three_fund_rebalance.formatting import ASSET_CLASS_LABELS, format_percent_prose
from three_fund_rebalance.models import (
    CENT,
    Account,
    FundType,
    Holding,
    Note,
    RebalanceResult,
    TargetAllocation,
    TaxTreatment,
    Trade,
    to_cents,
)

# Slack allowed when carrying one phase's optimal objective value forward as
# a "<=" bound for the next phase. HiGHS (scipy's default LP solver) is not
# bit-exact, so a hard "<=" against the raw optimum can spuriously reject the
# true optimum of the next phase over floating point noise. Don't set it to
# zero.
#
# Every carried bound is also a budget a later phase can spend: giving up
# this much of an earlier priority is allowed, so the volume-minimizing
# phase at the bottom will happily do it. At a cent, with five bounds
# stacked up, that surfaced as trades like "sell $5,999.99" where
# the answer is $6,000.00 -- the drift was under a cent per phase, but it
# landed just below the rounding boundary. A tenth of a cent stays well
# clear of HiGHS's noise (verified against portfolios from $100k to $8B)
# while sitting below the cent grid every displayed amount rounds to, so it
# cannot produce a visible artifact.
_OBJECTIVE_SLACK = 0.001

# Fund types whose dollar target we're solving for (CASH is excluded: it is
# not a security, and its target is implicitly zero -- see _build_slots).
# Bonds last is load-bearing, not alphabetical: compute_trades states
# _TARGET_FUND_TYPES[:-1] as equalities and lets the account budgets imply the
# third, so this tuple's order decides which class is the implied one.
_TARGET_FUND_TYPES = (FundType.US_STOCK, FundType.INTERNATIONAL_STOCK, FundType.US_BOND)
# The keys the dollar dicts are keyed by, defined once in allocation.py --
# note "bond", not FundType.US_BOND.value.
_TARGET_KEYS = ASSET_CLASS_KEYS


class RebalanceError(Exception):
    """Raised when the accounts cannot be solved at all -- an account with
    money and no fund declared to invest it in, duplicate account nicknames,
    or a mix of holdings no arrangement can reconcile with the resolved
    allocation.

    Not raised for a target the funds cannot reach: that is approximated as
    closely as the accounts allow and reported through `_capacity_notes`.
    """


@dataclass(frozen=True)
class _Slot:
    """One decision variable: how much to hold in `holding` within the
    account at `account_index`."""

    account_index: int
    holding: Holding

    @property
    def fund_type(self) -> FundType:
        return self.holding.fund_type


@dataclass(frozen=True)
class _ShelteredPurchase:
    """One extra decision variable measuring the dollars *bought* into one
    sheltered slot, ignoring any movement the other way.

    A wash sale is directional -- selling fund X in taxable only matters
    alongside buying fund X in a shelter -- and the absolute-value variables
    the other phases share cannot tell a buy from a sell. Only the buy side
    gets one of these. The taxable sale is the leg that realizes the loss,
    but it is also the leg phase 2 has already minimized and the one the
    portfolio usually has no choice about; what the shelter buys instead is
    free to change, so that is the lever phase 3 pulls. Penalizing the sale
    as well would put phase 3 in opposition to phase 1, which exists to sell
    exactly those taxable bonds.

    One is created only for a slot that could actually take part in a wash
    sale, so in the common case there are none at all.
    """

    slot_index: int


def _to_decimal(value: float) -> Decimal:
    # Route through str() to avoid dragging in float's binary-fraction noise
    # (Decimal(0.1) != Decimal("0.1")); six decimal places is far finer than
    # the cent precision we ultimately round to.
    return Decimal(str(round(value, 6)))


def _fund_type_coefficient(slot: _Slot, target_type: FundType) -> float:
    """`Holding.fraction_of` as the float the LP needs.

    The rule itself -- 1 for a direct match, the fund's own internal share for
    a target-date slot, 0 otherwise -- is stated once, on the holding. This is
    only the conversion: the LP necessarily works in floats, and this is one
    of the two boundaries where money crosses into them.

    Note `Holding.fraction_of` delegates to `TargetDateAllocation.fraction_of`
    rather than dividing the raw percentage by 100, because the three sleeves
    have to sum to exactly 1 or this slot's asset-class rows and its account's
    budget row contradict each other. See TargetDateAllocation.
    """
    return float(slot.holding.fraction_of(target_type))


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


def _slot_indices_by_account(slots: list[_Slot]) -> dict[int, list[int]]:
    """Which slot indices belong to which account, grouped in a single pass.

    Three separate places want this -- the capacity check, the LP's per-account
    budget rows, and the rounding pass that turns solved values back into
    trades. Each used to rescan the whole slot list once per account, which is
    O(accounts x slots) for what one pass answers. Nothing here is ever big
    enough for that to be slow; grouping once is just the shape the code
    already used in one of the three, and having the other two disagree read
    as an oversight rather than a decision.

    Accounts with no slots at all are absent, which is what every caller wants
    -- an account with nothing to invest contributes no rows.
    """
    grouped: dict[int, list[int]] = {}
    for index, slot in enumerate(slots):
        grouped.setdefault(slot.account_index, []).append(index)
    return grouped


def _check_names_unique(accounts: list[Account]) -> None:
    counts = Counter(a.name for a in accounts)
    duplicates = {name for name, count in counts.items() if count > 1}
    if duplicates:
        listed = ", ".join(repr(name) for name in sorted(duplicates))
        raise RebalanceError(f"Account names must be unique; duplicated: {listed}")


def _normalized_fund_name(holding: Holding) -> str:
    """The key two holdings must share to count as the same security. Case
    and surrounding space are noise a user shouldn't have to get right;
    anything beyond that is a judgment we can't make from a name alone --
    VTI and VTSAX track the same index and would be substantially identical
    to the IRS, but VTI and VOO merely look similar. Matching literally
    means the check never cries wolf, and the warning says what it misses."""
    return holding.name.strip().casefold()


def _wash_sale_variables(accounts: list[Account], slots: list[_Slot]) -> list[_ShelteredPurchase]:
    """One variable per sheltered slot holding a fund that is *also* held in
    a taxable account -- the only purchases that could complete a wash sale.
    Returns an empty list for the common portfolio where no fund name
    straddles that line, in which case phase 3 has nothing to do.

    A taxable holding of zero is skipped: you cannot sell what you do not
    own, so no purchase can pair with it into a wash sale. That matters --
    without the check, an empty taxable slot standing ready to receive a fund
    would suppress the very purchase phase 5 wants to make, over a sale that
    can never happen.

    Beyond that this is blind to whether the taxable side is actually sold,
    which no linear objective can condition on: whether a position shrinks is
    decided by the same solve. The residue is that phase 3 also mildly
    prefers not to accumulate, in a shelter, a fund you already hold in
    taxable. That costs nothing -- it sits below both objectives that spend
    money, and it points the same way as phases 4 and 5 far more often than
    not.
    """
    taxable_names: set[str] = set()
    sheltered_slots: dict[str, list[int]] = {}
    for index, slot in enumerate(slots):
        name = _normalized_fund_name(slot.holding)
        if not name:
            continue
        if accounts[slot.account_index].tax_treatment == TaxTreatment.TAXABLE:
            if slot.holding.value > 0:
                taxable_names.add(name)
        else:
            sheltered_slots.setdefault(name, []).append(index)

    return [
        _ShelteredPurchase(index)
        for name in sorted(taxable_names & set(sheltered_slots))
        for index in sheltered_slots[name]
    ]


def _asset_class_reach(
    accounts: list[Account],
    slots: list[_Slot],
    slots_by_account: dict[int, list[int]],
) -> dict[FundType, tuple[float, float]]:
    """The smallest and largest dollar total each asset class can reach given
    which fund types are declared where.

    Each account has to allocate exactly its own total across its own slots,
    so its contribution to one asset class is bounded by the smallest and the
    largest coefficient among those slots. Both bounds matter. An account
    holding a single fund -- a target-date fund, or one individual fund --
    has one coefficient, so its floor and ceiling are the same number:
    whatever that fund holds, the portfolio holds, and no target below that
    is reachable.

    This is a relaxation -- it bounds each class on its own, ignoring that an
    account has to satisfy all three at once -- so it is sound for rejecting
    the impossible, not for certifying the possible.
    """
    reach = {}
    for fund_type in _TARGET_FUND_TYPES:
        floor = ceiling = 0.0
        for account_index, indices in slots_by_account.items():
            total = float(accounts[account_index].total_value())
            coefficients = [_fund_type_coefficient(slots[i], fund_type) for i in indices]
            floor += total * min(coefficients)
            ceiling += total * max(coefficients)
        reach[fund_type] = (floor, ceiling)
    return reach


def _reachable_bounds(
    dollar_bounds: dict[str, tuple[Decimal, Decimal]],
    reach: dict[FundType, tuple[float, float]],
) -> dict[str, tuple[Decimal, Decimal]]:
    """The band as the *trigger* can actually use it: the user's own band,
    widened to the nearest reachable point for any class whose band the
    accounts cannot reach at all.

    A class pinned outside its band by what the accounts hold -- a
    target-date fund's bond sleeve against a 0% bond target, say -- is
    outside it forever, and a band that can never be satisfied is a band that
    never says "leave it alone". Every later run would then drive all three
    classes back to exact target and trade on any drift at all, which is the
    opposite of what a band is for. Widening to the reachable edge restores
    the quiet: the run that gets the class as close as it can go is the last
    run that trades.

    It cannot wave through a portfolio that could still do better. `reach` is
    a relaxation, so its floor is a valid lower bound on every achievable
    total: the widened edge sits at or below the true one, and the only
    current value inside the widened region is one already sitting on that
    bound. Where the band is reachable this is the band, unchanged.
    """
    widened = {}
    for fund_type in _TARGET_FUND_TYPES:
        key = _TARGET_KEYS[fund_type]
        low, high = dollar_bounds[key]
        floor, ceiling = reach[fund_type]
        if float(high) < floor:
            high = _to_decimal(floor)
        elif float(low) > ceiling:
            low = _to_decimal(ceiling)
        widened[key] = (low, high)
    return widened


def _capacity_notes(
    band_bounds: dict[str, tuple[Decimal, Decimal]],
    reachable_bounds: dict[str, tuple[Decimal, Decimal]],
    total_value: Decimal,
) -> list[Note]:
    """Say so when the funds the user holds cannot reach a target, rather
    than letting the plan quietly land somewhere else.

    The test is `_reachable_bounds` having had to widen that class: the band
    and what the accounts can hold do not overlap at all, so the class is
    outside its band whatever else the portfolio does. Nothing weaker will
    do. A class can also miss its band because the *other two* pinned the
    dollars it needed, and the closest allocation overall is then a matter of
    which class gives way -- true of the three together, but not a fact about
    this one, which on its own could have reached its target. The comparison
    table marks that class as outside its band and says no more; inventing a
    reason for it here would be inventing a false one.

    Every ceiling before any floor, as the fail-fast check this replaced
    ordered them. One account holding one fund breaches both at once, and
    "nothing you hold can be bonds" points at the missing piece, while "you
    are stuck holding this much U.S. stock" describes the same problem from
    the side the user can do least about.
    """
    notes = []
    for above in (False, True):
        for fund_type in _TARGET_FUND_TYPES:
            key = _TARGET_KEYS[fund_type]
            # Decimal, and rounded to six places by `_to_decimal` on the way
            # out of `_reachable_bounds` -- which is what keeps this test off
            # float noise. `reach` is computed in floats, so a class sitting
            # exactly on a reachable edge (a bond target set to a target-date
            # fund's own sleeve, with no band) misses it by a billionth of a
            # dollar and widens the bound by that much. Six places absorb it;
            # comparing the floats would report a reachable target as out of
            # reach.
            edge = reachable_bounds[key][1 if above else 0]
            if edge == band_bounds[key][1 if above else 0]:
                continue  # this class can reach its band; nothing to report
            label = ASSET_CLASS_LABELS[fund_type]
            share = edge / total_value * Decimal(100)
            # Three lines at the longest label and a ten-figure amount, which
            # is what fits without an indented second paragraph. Two things
            # went to get there. The target's own dollar figure, because the
            # comparison table two sections up prints it for every class in
            # dollars *and* as a share, so restating it here was a
            # restatement -- the label says this is about the target, and the
            # verb says which side of it the accounts are stuck on. And the
            # sentence explaining *why* they are stuck, which the remedy below
            # now names obliquely: a reader who does not already know that a
            # target-date fund's mix cannot be split will not learn it here.
            #
            # The label leads the note on screen, so it is capitalized the way
            # a sentence would be -- sliced rather than .capitalize()d, which
            # would lower-case the rest and leave "U.s. stock".
            notes.append(
                Note(
                    label=f"{label[0].upper()}{label[1:]} target out of reach",
                    summary=(
                        f"These accounts cannot hold less than ${edge:,.2f}, or "
                        f"{format_percent_prose(share)}% of the portfolio. Raise the target, "
                        "or hold less in single-fund and target-date accounts."
                        if above
                        else f"No combination of the funds held reaches more than "
                        f"${edge:,.2f}, or {format_percent_prose(share)}% of the portfolio. "
                        "Hold individual funds in a larger share of the portfolio."
                    ),
                )
            )
    return notes


def _current_asset_class_dollars(accounts: list[Account]) -> dict[str, Decimal]:
    """What each asset class is worth right now, target-date sleeves included."""
    holdings = [h for account in accounts for h in account.holdings]
    return {
        _TARGET_KEYS[fund_type]: sum((h.component(fund_type) for h in holdings), Decimal(0))
        for fund_type in _TARGET_FUND_TYPES
    }


def _reconcile_to_total(
    class_totals: dict[str, Decimal], total_value: Decimal
) -> dict[str, Decimal]:
    """Force the three asset-class totals to sum to exactly `total_value`.

    They do not, on their own: `_to_decimal` rounds each class to six decimal
    places independently, so two classes rounding down half a micro-dollar
    each leave the three summing to a millionth of a dollar less than the
    portfolio.

    `compute_trades` states only two of the three as equalities and lets the
    account budgets imply the third, so a gap here no longer decides whether
    the portfolio can be solved at all -- it decides whether the implied
    class lands on the figure settled here or a hair off it. Close it anyway:
    this function's whole contract is "what each asset class should be
    worth", and three amounts that do not add up to the portfolio are not
    that.

    The residue goes to the largest class, where it is orders of magnitude
    below the cent grid every displayed figure rounds to and so cannot
    surface as an artifact.
    """
    residue = total_value - sum(class_totals.values(), Decimal(0))
    if not residue:
        return class_totals
    largest = max(class_totals, key=lambda key: class_totals[key])
    return {**class_totals, largest: class_totals[largest] + residue}


# Both allocation-stage LPs share one variable layout, three columns per
# asset class:
#
#   [ p_0..p_2 | a_0..a_2 | b_0..b_2 ]
#     the class   distance   distance
#     total       from one   from a
#                 anchor     second anchor
#
# What the two anchors mean differs by caller -- _place_cash measures band
# violation then distance from target, _resolve_allocation measures distance
# from target then from where the portfolio already sits -- but the shape is
# the same, which is what lets both use the helpers below. The names exist
# because the alternative is a dozen hand-written `row[3 + index]` and
# `row[6 + index]` expressions whose only documentation is being read
# carefully.
_CLASS_TOTAL = 0
_FIRST_ANCHOR = 3
_SECOND_ANCHOR = 6
_ALLOCATION_WIDTH = 9


def _allocation_row(*terms: tuple[int, int, float]) -> list[float]:
    """One constraint row over the layout above. Each term is a (block,
    class index, coefficient) triple, so a row reads as the constraint it is
    rather than as index arithmetic."""
    row = [0.0] * _ALLOCATION_WIDTH
    for block, index, coefficient in terms:
        row[block + index] = coefficient
    return row


def _abs_value_rows(
    block: int, index: int, anchor: float
) -> tuple[list[tuple[list[float], float]], ...]:
    """The standard pair linearizing |p_i - anchor| into the variable at
    `block + index`: it is forced to at least the gap in either direction, so
    any objective that wants it small drives it to exactly the gap."""
    return (
        (_allocation_row((_CLASS_TOTAL, index, 1.0), (block, index, -1.0)), anchor),
        (_allocation_row((_CLASS_TOTAL, index, -1.0), (block, index, -1.0)), -anchor),
    )


def _place_cash(
    current: dict[str, Decimal],
    dollar_targets: dict[str, Decimal],
    band_bounds: dict[str, tuple[Decimal, Decimal]],
    reach: dict[FundType, tuple[float, float]],
    total_value: Decimal,
) -> dict[str, Decimal] | None:
    """Spend the uninvested cash and nothing else, or report that spending it
    is not enough. Returns the resulting class totals when the cash alone
    leaves every class inside its band, and `None` when it does not -- which
    is the caller's signal to rebalance properly.

    "Cash and nothing else" is the constraint `p >= current`: cash can only
    add to a class total, and any decrease would be a sale. Two objectives,
    lexicographic:

      1. Minimize how far outside its band each class is left.
      2. Among the ties, sit as close to target as possible.

    (1) rather than (2) first because the band is what the answer turns on:
    with two classes below target the cash could go to either and be equally
    close to target overall, and only one of those choices might get the
    laggard back inside its band. Asking for the band directly settles it,
    and (2) then places whatever is left over.
    """
    keys = [_TARGET_KEYS[fund_type] for fund_type in _TARGET_FUND_TYPES]

    # [ p_0..p_2 | v_0..v_2 | d_0..d_2 ]
    #  class total  outside   distance
    #               its band  from target
    bounds = []
    for fund_type in _TARGET_FUND_TYPES:
        floor = float(current[_TARGET_KEYS[fund_type]])
        ceiling = reach[fund_type][1]
        bounds.append((min(floor, ceiling), ceiling))
    bounds += [(0.0, None)] * 6

    A_eq = [_allocation_row(*((_CLASS_TOTAL, i, 1.0) for i in range(3)))]
    b_eq = [float(total_value)]

    A_ub, b_ub = [], []
    for index, key in enumerate(keys):
        low, high = band_bounds[key]
        # The band violation is not an absolute value around one anchor but a
        # gap outside a range: v_i >= low - p_i and v_i >= p_i - high. The two
        # cannot both bind, so a single variable measures the violation in
        # whichever direction it happens to fall, and a class inside its band
        # drives it to zero.
        A_ub.append(
            _allocation_row((_CLASS_TOTAL, index, -1.0), (_FIRST_ANCHOR, index, -1.0))
        )
        b_ub.append(-float(low))
        A_ub.append(_allocation_row((_CLASS_TOTAL, index, 1.0), (_FIRST_ANCHOR, index, -1.0)))
        b_ub.append(float(high))

        for row, bound in _abs_value_rows(_SECOND_ANCHOR, index, float(dollar_targets[key])):
            A_ub.append(row)
            b_ub.append(bound)

    inside_the_band = [0.0] * 3 + [1.0] * 3 + [0.0] * 3
    toward_target = [0.0] * 6 + [1.0] * 3

    solution = _solve(
        inside_the_band, A_eq, b_eq, A_ub, b_ub, bounds, "putting the available cash to work"
    )
    if solution.fun > _OBJECTIVE_SLACK:
        return None  # cash alone cannot settle this; the caller rebalances.

    A_ub.append(inside_the_band)
    b_ub.append(solution.fun + _OBJECTIVE_SLACK)
    solution = _solve(
        toward_target,
        A_eq,
        b_eq,
        A_ub,
        b_ub,
        bounds,
        "investing the available cash where it is furthest below target",
    )
    return {key: _to_decimal(solution.x[index]) for index, key in enumerate(keys)}


def _resolve_allocation(
    current: dict[str, Decimal],
    dollar_targets: dict[str, Decimal],
    band_bounds: dict[str, tuple[Decimal, Decimal]],
    reach: dict[FundType, tuple[float, float]],
    total_value: Decimal,
) -> dict[str, Decimal]:
    """Decide what each asset class should be worth, before deciding where to
    hold it.

    **The band is a trigger, not a destination.** Nothing moves while every
    class sits inside its band and there is no cash waiting to be invested;
    once anything trips that test, the whole portfolio goes back to target.
    Stopping at the band edge instead -- which is what this did once -- leaves
    the portfolio on the boundary, where the next small drift trips the band
    again, and it is under-determined besides: a class one point out of band
    can be brought back by selling either of the other two, at identical cost,
    so which one got sold came down to whichever vertex HiGHS happened to
    return.

    Cash is handled by the same rule and needs no special case. It is never a
    decision variable -- each account's budget is an equality, so every dollar
    of it is spent -- and the objective below steers it at whatever is
    furthest below target. A portfolio whose cash alone brings every class
    back inside its band therefore invests that cash and stops, because the
    only sale that could follow would move it *away* from target.

    Two objectives, lexicographic, and the second one only ever runs when the
    target is unreachable:

      1. Sit as close to target as the accounts allow.
      2. Among the ties, move as little as possible from where the portfolio
         already sits.

    (2) exists because an account holding a single fund pins that fund's share
    of the portfolio, so the exact target can be out of reach -- and the
    closest reachable points to it are then a whole face of the polytope, not
    a vertex. With U.S. stock pinned at 60% against a 50/25/25 target, every
    split of the remaining 40% between international and bonds is exactly as
    far from target as every other. Staying near where the portfolio already
    is settles that without trading for nothing.

    "As close as the accounts allow" is the whole answer in that case: an
    unreachable target is approximated, never refused. `band_bounds` is only
    ever read above, by the trigger -- past it the bounds are `reach` alone.
    The band used to be the LP's bounds as well, which made it the arbiter of
    feasibility: a target the funds could not reach was an error unless the
    band happened to be wide enough to cover the gap, so widening a band
    silently converted a refusal into a plan. `compute_trades` passes the
    band `_reachable_bounds` has widened, so a class the accounts pin outside
    its band still settles rather than re-triggering forever.

    Running this whole stage before the location phases is not an
    optimization, it is what keeps the band honest. Those phases are *stated*
    as "minimize this asset class in that kind of account", which only means
    "relocate it" while the class total is fixed. Left as a range, they can
    satisfy themselves by holding less of the asset class outright -- so a
    portfolio that was merely a little heavy in international would have it
    sold off rather than moved, and a portfolio underweight bonds would have
    its bond fund liquidated to clear tax-free space. Pinning the totals here
    restores the equality those phases assume, and costs them nothing they
    should have: moving a holding between accounts never changes an asset
    class total.
    """
    keys = [_TARGET_KEYS[fund_type] for fund_type in _TARGET_FUND_TYPES]

    # The trigger, in Decimal and before any solve: every class inside its
    # band, and nothing uninvested. Both halves matter -- cash is always put
    # to work, so its presence alone is enough to reopen the question.
    # `band_bounds` is the band already clamped to [0, total_value], so a
    # band wide enough to cover everything answers "inside" for everything,
    # which is what a band that wide means.
    # To the cent, because that is the grid money is entered and traded on.
    # A target-date fund's sleeves are normalized fractions rather than exact
    # decimals, so the components can miss the account's total by a rounding
    # artifact many orders of magnitude below a penny; read literally, that
    # dust would count as cash and send a portfolio sitting on its target
    # into _place_cash for no reason.
    uninvested = to_cents(total_value - sum(current.values(), Decimal(0)))
    if uninvested <= 0:
        if all(band_bounds[key][0] <= current[key] <= band_bounds[key][1] for key in keys):
            # Say it exactly, with the numbers the portfolio already holds,
            # rather than handing an LP a solver's-worth of slack to spend
            # drifting a fraction of a cent. The location phases still run:
            # this fixes the totals, and rearranging *within* them is free.
            return _reconcile_to_total(dict(current), total_value)
    else:
        # Cash first, then the trigger -- the band is asked about the
        # portfolio the cash leaves behind, not the one holding it. Testing
        # the cash itself instead would mean a few cents swept up from a
        # dividend rebalanced an entire portfolio that was comfortably
        # inside its band, which is the opposite of what a band is for.
        after_cash = _place_cash(current, dollar_targets, band_bounds, reach, total_value)
        if after_cash is not None:
            return _reconcile_to_total(after_cash, total_value)

    # p_0..p_2 are the class totals; the rest track distance from current and
    # from target, by the same absolute-value linearization used elsewhere.
    #
    # The bounds are what the accounts can hold, and nothing else: past the
    # trigger the band has had its say, and the objective below aims at the
    # target the band brackets anyway. Constraining these to the band as well
    # is what used to turn a target the accounts cannot quite reach into a
    # refusal to plan at all.
    #
    # This box always meets the equality below, so there is no infeasible
    # case here to reject: every slot's three coefficients sum to 1, so each
    # account's three smallest coefficients sum to at most 1 and its three
    # largest to at least 1 -- which puts the sum of the floors at or below
    # the portfolio and the sum of the ceilings at or above it.
    bounds = [reach[fund_type] for fund_type in _TARGET_FUND_TYPES]
    bounds += [(0.0, None)] * 6

    A_eq = [_allocation_row(*((_CLASS_TOTAL, i, 1.0) for i in range(3)))]
    b_eq = [float(total_value)]

    A_ub, b_ub = [], []
    for block, anchor in ((_FIRST_ANCHOR, current), (_SECOND_ANCHOR, dollar_targets)):
        for index, key in enumerate(keys):
            for row, bound in _abs_value_rows(block, index, float(anchor[key])):
                A_ub.append(row)
                b_ub.append(bound)

    stay_put = [0.0] * 3 + [1.0] * 3 + [0.0] * 3
    toward_target = [0.0] * 6 + [1.0] * 3

    solution = _solve(
        toward_target, A_eq, b_eq, A_ub, b_ub, bounds, "bringing the allocation back to target"
    )
    if solution.fun > _OBJECTIVE_SLACK:
        # The target itself is out of reach, so its closest reachable points
        # are a face rather than a vertex. Pick the one nearest to where the
        # portfolio already sits. (Skipped when the target *is* reachable:
        # the first objective pins the answer outright, and a second solve
        # could only spend its slack drifting off an exact figure.)
        A_ub.append(toward_target)
        b_ub.append(solution.fun + _OBJECTIVE_SLACK)
        solution = _solve(
            stay_put,
            A_eq,
            b_eq,
            A_ub,
            b_ub,
            bounds,
            "getting the allocation as close to target as the funds held allow",
        )
    return _reconcile_to_total(
        {key: _to_decimal(solution.x[index]) for index, key in enumerate(keys)}, total_value
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
    #
    # The bound is written to be *safe* rather than tight, but it is worth
    # knowing it is also small. `residual` is what independent per-slot
    # rounding left over against the account's own total, so it is under half
    # a cent per slot -- a handful of cents at the outside, and this runs in
    # single-digit iterations. Nothing enforces that; a future caller passing
    # a large residual would get O(cents x slots) rather than a hang.
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
) -> tuple[dict[int, Decimal], int]:
    """Turn one account's solved (fractional) slot values into final cent
    amounts that both round cleanly and leave the account summing to exactly
    its own total.

    Two things have to hold together here, and doing them in sequence breaks
    them: rounding each slot independently can leave an account a cent off
    its real value, and dropping a sub-minimum trade afterwards reopens the
    same gap from the other side. Together those produced trades
    like "buy $5,000.01" against exactly $5,000.00 of cash -- the offsetting
    $0.01 sell was filtered out as too small while the extra cent of buying
    survived.

    So slots that end up trading less than the minimum are snapped back to
    their current value and the freed money is redistributed among the
    slots that are still trading, repeating until nothing new falls below
    the minimum. Preserving the per-account total is the hard constraint --
    you cannot spend money you don't have -- while the aggregate allocation
    targets are approximate goals, so any leftover cent is absorbed there.

    Returns the final values and a count of the moves dropped for being too
    small -- moves the solver actually wanted, not slots that were never
    going to trade, which is the distinction the report has to disclose.
    """
    target_total = to_cents(account.total_value())
    current = {i: to_cents(slots[i].holding.value) for i in indices}
    held: set[int] = set()  # slots pinned at their current value (no trade)
    dropped = 0

    # Each pass pins at least one more slot, so this runs at most once per slot.
    for _ in range(len(indices) + 1):
        tradeable = [i for i in indices if i not in held]
        if not tradeable:
            return current, dropped

        budget = target_total - sum((current[i] for i in held), Decimal(0))
        values = {i: to_cents(raw_values[i]) for i in tradeable}
        _distribute_residual(values, raw_values, budget - sum(values.values()))

        too_small = [i for i in tradeable if abs(values[i] - current[i]) < MIN_TRADE_DOLLARS]
        if not too_small:
            return {**{i: current[i] for i in held}, **values}, dropped
        dropped += sum(1 for i in too_small if values[i] != current[i])
        held.update(too_small)

    # Unreachable: every pass either returns or pins at least one more slot,
    # so the loop runs out of tradeable slots (and returns above) first. Kept
    # as a backstop so a future change can't fall through to an implicit None.
    return current, dropped  # pragma: no cover


def _solve(c, A_eq, b_eq, A_ub, b_ub, bounds, context: str):
    result = linprog(c=c, A_eq=A_eq, b_eq=b_eq, A_ub=A_ub, b_ub=b_ub, bounds=bounds, method="highs")
    if not result.success:
        # `context` says what the solver was doing in the user's terms, not
        # in the vocabulary of the phase list -- this message is printed
        # straight to whoever is running the CLI.
        raise RebalanceError(
            f"no arrangement of the funds held reaches the target while {context}. "
            f"(Solver detail: {result.message})"
        )
    return result


def _wash_sale_notes(accounts: list[Account], trades: list[Trade]) -> list[Note]:
    """Flag any fund this plan sells in a taxable account while buying it in
    a sheltered one. Phase 3 avoids this arrangement whenever an equally good
    alternative exists, so anything left here was unavoidable given what the
    accounts hold -- which is exactly when the user needs to be told.

    States the finding and stops. This used to recite section 1091's window
    and standard and the IRS's position on a replacement bought inside an
    IRA, which ran to seven lines -- the single largest block below the
    orders, and statute rather than anything about this portfolio. The
    conditional "may be" stays: the tool cannot see cost basis, trade dates,
    or purchases made anywhere else in the window, so it flags the shape and
    never asserts the conclusion.
    """
    treatment = {a.name: a.tax_treatment for a in accounts}
    sold_in_taxable: dict[str, Decimal] = {}
    bought_in_shelter: dict[str, Decimal] = {}
    display: dict[str, str] = {}

    for trade in trades:
        key = trade.fund_name.strip().casefold()
        if not key:
            continue
        display.setdefault(key, trade.fund_name.strip())
        taxable = treatment[trade.account_name] == TaxTreatment.TAXABLE
        if taxable and trade.action == "sell":
            sold_in_taxable[key] = sold_in_taxable.get(key, Decimal(0)) + trade.amount
        elif not taxable and trade.action == "buy":
            bought_in_shelter[key] = bought_in_shelter.get(key, Decimal(0)) + trade.amount

    notes = []
    for key in sorted(set(sold_in_taxable) & set(bought_in_shelter)):
        overlap = min(sold_in_taxable[key], bought_in_shelter[key])
        notes.append(
            Note(
                label="Wash sale",
                summary=f"This plan sells {display[key]} in a taxable account and buys it "
                f"in a tax-advantaged one, overlapping by ${overlap:,}. If any of those "
                "shares are at a loss this may be a wash sale.",
            )
        )
    return notes


def _international_location_notes(accounts: list[Account], trades: list[Trade]) -> list[Note]:
    """Why international stock was bought in a tax-advantaged account.

    Phase 5 prefers international in taxable, where its foreign withholding
    is claimable as a credit. Phase 2 outranks it, so an order that puts
    international into a shelter is the visible result of a preference the
    reader was never shown losing -- the one place the plan looks contrary to
    what it optimizes for. Saying so is the difference between a deliberate
    trade-off and an apparent bug.

    Fired on the *buy*, not on the residue. An account that already holds
    international in a shelter is the common case and reports nothing worth
    reading -- the note that fires either way is the one a reader learns to
    skip. What is surprising is the plan moving it there, and that is the
    only thing this reports.

    Silent when nothing is taxable, where there is no alternative to describe.
    `fund_type`, not `fraction_of`, for the same reason phase 5 reads
    `slot.fund_type` directly: a target-date fund is not majority-foreign and
    passes no credit through from either kind of account, so its
    international sleeve is not what this is about.
    """
    if not any(a.tax_treatment == TaxTreatment.TAXABLE for a in accounts):
        return []
    treatment = {a.name: a.tax_treatment for a in accounts}
    bought = sum(
        (
            trade.amount
            for trade in trades
            if trade.action == "buy"
            and trade.fund_type == FundType.INTERNATIONAL_STOCK
            and treatment[trade.account_name] != TaxTreatment.TAXABLE
        ),
        Decimal(0),
    )
    if bought <= 0:
        return []
    return [
        Note(
            label="International in tax-advantaged",
            # What it costs and why it happened anyway, both in the reader's
            # terms. An earlier draft ended "Avoiding a taxable sale ranks
            # higher", which is the solver's vocabulary and not the reader's
            # -- a phase ranking means nothing to someone reading a plan, and
            # the concrete alternative it would have taken says the same
            # thing. "A foreign tax credit" stays indefinite because whether
            # one could be claimed is the reader's situation, not ours.
            summary=f"Buying ${bought:,} of international stocks in tax-advantaged "
            "accounts gives up a foreign tax credit. Buying them in a taxable account "
            "would have meant selling something there.",
        )
    ]


def _location_objectives(
    accounts: list[Account], slots: list[_Slot], n: int, k: int
) -> list[tuple[list[float], str]]:
    """The six location phases, in priority order, as vectors over the shared
    variable layout `[ x (n) | y (n) | w (k) ]`.

    Pure data: every one is a cost vector and the sentence `_solve` prints if
    that phase turns out to be infeasible. The ranking *is* the design -- it
    is what decides that a couple of basis points of foreign tax credit never
    justifies opening a taxable trade -- so it reads better as a list you can
    take in at once than as forty lines wedged between the constraint rows
    and the rounding pass. The module docstring is the long form.

    Each phase's optimum is carried forward by the caller as a `<=` bound, so
    a phase below can refine an earlier one's answer but never undo it.
    """
    taxable = [accounts[s.account_index].tax_treatment == TaxTreatment.TAXABLE for s in slots]
    tax_free = [accounts[s.account_index].tax_treatment == TaxTreatment.TAX_FREE for s in slots]

    def over_slots(weights: list[float]) -> list[float]:
        """A cost on the slot values themselves -- the x block."""
        return weights + [0.0] * (n + k)

    def over_movement(weights: list[float]) -> list[float]:
        """A cost on how far a slot moves -- the y block, which the absolute
        value rows pin to |x - current|."""
        return [0.0] * n + weights + [0.0] * k

    return [
        (
            over_slots(
                [
                    _fund_type_coefficient(slot, FundType.US_BOND) if is_taxable else 0.0
                    for slot, is_taxable in zip(slots, taxable, strict=True)
                ]
            ),
            "moving bonds out of taxable accounts",
        ),
        (
            over_movement([1.0 if is_taxable else 0.0 for is_taxable in taxable]),
            "holding down the amount sold in taxable accounts",
        ),
        (
            [0.0] * (2 * n) + [1.0] * k,
            "steering clear of a wash sale",
        ),
        (
            over_slots(
                [
                    _fund_type_coefficient(slot, FundType.US_BOND) if is_tax_free else 0.0
                    for slot, is_tax_free in zip(slots, tax_free, strict=True)
                ]
            ),
            "holding bonds in tax-deferred rather than tax-free accounts",
        ),
        (
            over_slots(
                [
                    # slot.fund_type directly, not _fund_type_coefficient: a
                    # target-date fund is not majority-foreign and passes no
                    # credit through, so counting its international sleeve
                    # would have the solver liquidate half a TDF for nothing.
                    1.0
                    if slot.fund_type == FundType.INTERNATIONAL_STOCK and not is_taxable
                    else 0.0
                    for slot, is_taxable in zip(slots, taxable, strict=True)
                ]
            ),
            "holding international stock in taxable accounts",
        ),
        (
            over_movement([1.0] * n),
            "keeping the total amount traded down",
        ),
    ]


def compute_trades(
    accounts: list[Account],
    target: TargetAllocation,
    band_pct: Decimal = Decimal(0),
    relative_band_pct: Decimal | None = None,
) -> RebalanceResult:
    """Solve for the trades that bring `accounts` to `target`, leaving any
    asset class alone while it is inside the band `allocation`'s
    `effective_band_points` allows it. The default of zero is the exact
    target."""
    _check_names_unique(accounts)

    total_value = sum((a.total_value() for a in accounts), Decimal(0))
    if total_value <= 0:
        return RebalanceResult(trades=[], notes=[], taxable_bond_dollars=Decimal(0))

    dollar_targets = target_dollar_amounts(target, total_value)
    dollar_bounds = target_dollar_bounds(target, total_value, band_pct, relative_band_pct)
    slots = _build_slots(accounts)
    # n cannot be 0 here: total_value > 0 (checked above) means at least one
    # account has a positive value, and _build_slots already rejects any
    # positive-value account that declares no tradeable holdings.
    n = len(slots)

    slots_by_account = _slot_indices_by_account(slots)
    reach = _asset_class_reach(accounts, slots, slots_by_account)

    # What each asset class should be worth is settled here, once, before any
    # objective about *where* to hold it gets a say -- see _resolve_allocation.
    # The trigger reads the band widened to what the accounts can reach; the
    # notes below read the band as the user set it, which is the one that
    # says whether the target was met.
    reachable_bounds = _reachable_bounds(dollar_bounds, reach)
    resolved = _resolve_allocation(
        _current_asset_class_dollars(accounts),
        dollar_targets,
        reachable_bounds,
        reach,
        total_value,
    )
    capacity_notes = _capacity_notes(dollar_bounds, reachable_bounds, total_value)

    # --- variable layout -------------------------------------------------
    # [ x_0..x_n-1 | y_0..y_n-1 | w_0..w_k-1 ]
    #   slot value  |x - current|  one-directional trade sizes (phase 3)
    # Built once and shared by every phase, so an objective is just a vector
    # over the same columns and a solved optimum is a row appended to A_ub.
    wash_variables = _wash_sale_variables(accounts, slots)
    k = len(wash_variables)
    width = 2 * n + k

    current = [float(slot.holding.value) for slot in slots]
    bounds = (
        [(0.0, float(accounts[s.account_index].total_value())) for s in slots]
        + [(0.0, None)] * n
        + [(0.0, None)] * k
    )

    def row() -> list[float]:
        return [0.0] * width

    # --- equalities: each account spends exactly its own total -----------
    A_eq, b_eq = [], []
    for account_index, account in enumerate(accounts):
        indices = slots_by_account.get(account_index)
        if not indices:
            continue
        budget = row()
        for i in indices:
            budget[i] = 1.0
        A_eq.append(budget)
        b_eq.append(float(account.total_value()))

    # --- inequalities shared by every phase ------------------------------
    A_ub, b_ub = [], []

    # Each asset class hits the total _resolve_allocation settled on. An
    # equality, not a pair of inequalities meeting in the middle: the two
    # describe the same set, but not to the solver, which is free to land a
    # fraction of a cent inside a matched pair -- and with several phases of
    # carried-forward objective slack above them, that fraction survives to
    # the final rounding as an exact $40,000.00 turned into "$39,999.99".
    #
    # Only two of the three are stated. The third is implied -- every slot's
    # three coefficients sum to 1, so adding the class rows together
    # reproduces the account budget rows above -- and *stating* an implied
    # row is not free: it is satisfiable only if the two sides agree to the
    # last bit, which floating point will not do. Coefficients that sum to
    # 1 + 1e-16 are enough to make a portfolio infeasible outright once the
    # portfolio is large enough for that relative error to exceed the
    # solver's absolute feasibility tolerance, which at HiGHS's 1e-7 is
    # somewhere below $8B. Left implicit, the same error just puts a
    # billionth of a cent of that portfolio in the wrong asset class.
    #
    # _resolve_allocation guarantees the three totals sum to the portfolio,
    # so the implied class lands on its resolved figure, not merely near it.
    for fund_type in _TARGET_FUND_TYPES[:-1]:
        aggregate = row()
        for i, slot in enumerate(slots):
            aggregate[i] = _fund_type_coefficient(slot, fund_type)
        A_eq.append(aggregate)
        b_eq.append(float(resolved[_TARGET_KEYS[fund_type]]))

    # Standard LP linearization of absolute value: y_i >= x_i - current_i and
    # y_i >= current_i - x_i force y_i to exactly |x_i - current_i| at the
    # optimum of any objective that wants y_i small.
    for i in range(n):
        rising = row()
        rising[i], rising[n + i] = 1.0, -1.0
        A_ub.append(rising)  # x_i - y_i <= current_i
        b_ub.append(current[i])

        falling = row()
        falling[i], falling[n + i] = -1.0, -1.0
        A_ub.append(falling)  # -x_i - y_i <= -current_i
        b_ub.append(-current[i])

    # Half of the same trick, for the wash-sale variables: only w_j >= x_i -
    # current_i is stated, so w_j tracks dollars bought and a slot moving the
    # other way contributes nothing to phase 3.
    for j, purchase in enumerate(wash_variables):
        i = purchase.slot_index
        bought = row()
        bought[i], bought[2 * n + j] = 1.0, -1.0
        A_ub.append(bought)  # x_i - w_j <= current_i
        b_ub.append(current[i])

    phases = _location_objectives(accounts, slots, n, k)

    solution = None
    for objective, context in phases:
        # An objective with no nonzero coefficient has nothing to say about
        # this portfolio -- no taxable accounts, no wash-sale exposure, no
        # Roth. Solving it would only re-find a feasible point, and the bound
        # it carried forward would be vacuous. Phase 6 always has something
        # to minimize, so `solution` is never left unset.
        if not any(objective):
            continue
        solution = _solve(objective, A_eq, b_eq, A_ub, b_ub, bounds, context)
        A_ub.append(objective)
        b_ub.append(solution.fun + _OBJECTIVE_SLACK)

    raw_values = [_to_decimal(v) for v in solution.x[:n]]
    new_values = [Decimal(0)] * n
    dropped_trades = 0
    for account_index, account in enumerate(accounts):
        indices = slots_by_account.get(account_index)
        if not indices:
            continue
        finalized, dropped = _finalize_account_values(account, indices, slots, raw_values)
        dropped_trades += dropped
        for i, value in finalized.items():
            new_values[i] = value

    # _finalize_account_values already snapped every sub-minimum move back to
    # its current value, so any remaining delta is a real, fillable trade.
    trades = []
    for slot, new_value in zip(slots, new_values, strict=True):
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
    for slot, new_value in zip(slots, new_values, strict=True):
        if accounts[slot.account_index].tax_treatment != TaxTreatment.TAXABLE:
            continue
        # `fraction_of`, not `_fund_type_coefficient`: this is a dollar
        # amount the report prints, so it stays in Decimal rather than
        # round-tripping the sleeve through a float on the way back out.
        taxable_bond_dollars += new_value * slot.holding.fraction_of(FundType.US_BOND)
    taxable_bond_dollars = to_cents(taxable_bond_dollars)

    # Capacity first: it is about the target itself, where the two below are
    # about the orders that chase it.
    notes = list(capacity_notes)
    if taxable_bond_dollars > 0:
        notes.append(
            Note(
                label="Bonds in taxable",
                # The bound sits with the figure it qualifies, and the cause
                # follows -- three lines with no second paragraph. "That can
                # only be held whole" is what the sentence could spare.
                summary=f"${taxable_bond_dollars:,} in bonds will stay in taxable accounts, "
                "the least these accounts allow. Either the tax-advantaged accounts are "
                "full, or those bonds sit inside a target-date fund.",
            )
        )
    notes.extend(_international_location_notes(accounts, trades))
    notes.extend(_wash_sale_notes(accounts, trades))

    return RebalanceResult(
        trades=trades,
        notes=notes,
        taxable_bond_dollars=taxable_bond_dollars,
        dropped_trades=dropped_trades,
    )
