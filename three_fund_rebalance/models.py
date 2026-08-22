"""Core data model for the three-fund rebalancer.

Everything in this module is a plain, immutable-ish dataclass with just enough
validation to keep the rest of the codebase (persistence, the LP engine,
reporting) from having to re-check basic invariants. All money is represented
as `Decimal`, never `float`, to avoid binary floating point rounding creeping
into dollar amounts a user might act on.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal
from enum import Enum

CENT = Decimal("0.01")
# Tolerance used when checking that a set of percentages "sums to 100" -
# percentages are typically entered to one decimal place by a human, so a
# small epsilon avoids rejecting e.g. 33.3 + 33.3 + 33.4.
PERCENT_SUM_TOLERANCE = Decimal("0.1")


def to_cents(amount: Decimal) -> Decimal:
    """Round a Decimal dollar amount to the nearest cent (banker's-unfriendly,
    i.e. standard "round half up") for display and for reconciling LP output."""
    return amount.quantize(CENT, rounding=ROUND_HALF_UP)


class FundType(Enum):
    """The kinds of holdings a slot in an account can be. TARGET_DATE is a
    single position that internally bundles U.S. stocks, international
    stocks, and bonds in a fixed ratio the user supplies via
    TargetDateAllocation. CASH is cash sitting available in the account,
    which the rebalance engine always targets down to zero (i.e. "invest all
    of it")."""

    US_STOCK = "us_stock"
    INTERNATIONAL_STOCK = "international_stock"
    US_BOND = "us_bond"
    TARGET_DATE = "target_date"
    CASH = "cash"


# The three asset classes held as separate funds. An account holds some
# combination of these *or* a single target-date fund, never both -- see
# Account.__post_init__.
INDIVIDUAL_FUND_TYPES = (
    FundType.US_STOCK,
    FundType.INTERNATIONAL_STOCK,
    FundType.US_BOND,
)

# Fund types that represent a single, directly tradeable fund with its own
# name/ticker (as opposed to CASH, which is not a security).
TRADEABLE_FUND_TYPES = (*INDIVIDUAL_FUND_TYPES, FundType.TARGET_DATE)


class TaxTreatment(Enum):
    """How an account is taxed. Two shelters, not one: they are equally
    exempt from tax *today*, but they differ in what a dollar of growth
    inside them is worth, which is what decides where bonds belong.

    TAX_DEFERRED (traditional 401(k)/IRA, 403(b), 457(b), SEP, SIMPLE) is
    taxed as ordinary income on the way out, so growth there is shared with
    the government -- the natural home for the low-return asset class.
    TAX_FREE (Roth IRA, Roth 401(k), HSA) never taxes qualified withdrawals,
    so it is the most valuable space in the portfolio and should hold the
    highest expected return, i.e. stocks. TAXABLE is a regular brokerage
    account.

    Anything that is not TAXABLE is a shelter; `Account.is_tax_advantaged`
    is the test for that, and the solver's taxable-vs-sheltered objectives
    are written as comparisons against TAXABLE so they read the same way.
    """

    TAXABLE = "taxable"
    TAX_DEFERRED = "tax_deferred"
    TAX_FREE = "tax_free"


@dataclass(frozen=True)
class TargetDateAllocation:
    """The mix of U.S. stocks, international stocks, and bonds held inside a
    target-date fund, as reported by the fund's fact sheet. Percentages must
    sum to ~100."""

    us_stock_pct: Decimal
    international_stock_pct: Decimal
    bond_pct: Decimal

    def __post_init__(self) -> None:
        for field_name in ("us_stock_pct", "international_stock_pct", "bond_pct"):
            value = getattr(self, field_name)
            if value < 0:
                raise ValueError(f"TargetDateAllocation.{field_name} cannot be negative: {value}")
        total = self.us_stock_pct + self.international_stock_pct + self.bond_pct
        if abs(total - Decimal(100)) > PERCENT_SUM_TOLERANCE:
            raise ValueError(
                "TargetDateAllocation percentages must sum to 100 "
                f"(got {total}: us_stock={self.us_stock_pct}, "
                f"international_stock={self.international_stock_pct}, bond={self.bond_pct})"
            )


@dataclass(frozen=True)
class Holding:
    """A single fund (or cash) position within one account."""

    fund_type: FundType
    name: str
    value: Decimal
    # Required if and only if fund_type is TARGET_DATE.
    target_date_allocation: TargetDateAllocation | None = None

    def __post_init__(self) -> None:
        if self.value < 0:
            raise ValueError(f"Holding value cannot be negative: {self.value}")
        if self.fund_type == FundType.TARGET_DATE and self.target_date_allocation is None:
            raise ValueError("A target-date fund holding requires a target_date_allocation")
        if self.fund_type != FundType.TARGET_DATE and self.target_date_allocation is not None:
            raise ValueError(
                f"Only target-date fund holdings may carry a target_date_allocation, "
                f"got {self.fund_type}"
            )
        if self.fund_type != FundType.CASH and not self.name.strip():
            raise ValueError("A fund holding must have a non-empty name/ticker")

    def us_stock_component(self) -> Decimal:
        """Dollar amount of this holding attributable to U.S. stocks."""
        if self.fund_type == FundType.US_STOCK:
            return self.value
        if self.fund_type == FundType.TARGET_DATE:
            return self.value * self.target_date_allocation.us_stock_pct / Decimal(100)
        return Decimal(0)

    def international_stock_component(self) -> Decimal:
        """Dollar amount of this holding attributable to international stocks."""
        if self.fund_type == FundType.INTERNATIONAL_STOCK:
            return self.value
        if self.fund_type == FundType.TARGET_DATE:
            return self.value * self.target_date_allocation.international_stock_pct / Decimal(100)
        return Decimal(0)

    def bond_component(self) -> Decimal:
        """Dollar amount of this holding attributable to bonds."""
        if self.fund_type == FundType.US_BOND:
            return self.value
        if self.fund_type == FundType.TARGET_DATE:
            return self.value * self.target_date_allocation.bond_pct / Decimal(100)
        return Decimal(0)


@dataclass
class Account:
    """One investment account: a 401(k), an IRA, a taxable brokerage account,
    etc. `name` is a user-chosen nickname that must be unique across the
    whole portfolio (this is how multiple accounts of the same type, e.g.
    two 401(k)s from different employers, are distinguished).

    An account holds *either* a single target-date fund or some combination
    of the three individual funds -- never a mix of the two. Cash is allowed
    alongside either.
    """

    account_type: str
    name: str
    tax_treatment: TaxTreatment
    holdings: list[Holding] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError("Account name cannot be empty")
        fund_types = {h.fund_type for h in self.holdings}
        if FundType.TARGET_DATE in fund_types and fund_types & set(INDIVIDUAL_FUND_TYPES):
            mixed = ", ".join(
                sorted(f.value for f in fund_types & {FundType.TARGET_DATE, *INDIVIDUAL_FUND_TYPES})
            )
            raise ValueError(
                f"Account {self.name!r} holds both a target-date fund and individual "
                f"funds ({mixed}); an account holds one or the other, not a mix"
            )

        seen_types = set()
        for holding in self.holdings:
            if holding.fund_type in seen_types:
                raise ValueError(
                    f"Account {self.name!r} has more than one "
                    f"{holding.fund_type.value} holding; only one per fund type is supported"
                )
            seen_types.add(holding.fund_type)

    def total_value(self) -> Decimal:
        return sum((h.value for h in self.holdings), Decimal(0))

    def get_holding(self, fund_type: FundType) -> Holding | None:
        return next((h for h in self.holdings if h.fund_type == fund_type), None)

    def available_cash(self) -> Decimal:
        holding = self.get_holding(FundType.CASH)
        return holding.value if holding else Decimal(0)

    def is_tax_advantaged(self) -> bool:
        return self.tax_treatment != TaxTreatment.TAXABLE


@dataclass(frozen=True)
class TargetAllocation:
    """The whole-portfolio target mix, in percent, across the three fund
    types. Derived from a stock and bond target plus VT's U.S. allocation --
    see allocation.py."""

    us_stock_pct: Decimal
    international_stock_pct: Decimal
    bond_pct: Decimal

    def __post_init__(self) -> None:
        for field_name in ("us_stock_pct", "international_stock_pct", "bond_pct"):
            value = getattr(self, field_name)
            if value < 0:
                raise ValueError(f"TargetAllocation.{field_name} cannot be negative: {value}")
        total = self.us_stock_pct + self.international_stock_pct + self.bond_pct
        if abs(total - Decimal(100)) > PERCENT_SUM_TOLERANCE:
            raise ValueError(f"TargetAllocation percentages must sum to 100 (got {total})")


@dataclass(frozen=True)
class Trade:
    """One buy or sell of a specific fund within a specific
    account. `amount` is always positive; `action` says which direction."""

    account_name: str
    fund_type: FundType
    fund_name: str
    action: str  # "buy" or "sell"
    amount: Decimal

    def __post_init__(self) -> None:
        if self.action not in ("buy", "sell"):
            raise ValueError(f"Trade.action must be 'buy' or 'sell', got {self.action!r}")
        if self.amount <= 0:
            raise ValueError(f"Trade.amount must be positive, got {self.amount}")
