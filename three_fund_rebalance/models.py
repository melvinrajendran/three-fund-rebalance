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
    """The kinds of holdings a slot in an account can be. TDF (target-date
    fund) is a single position that internally bundles domestic equity,
    international equity, and bonds in a fixed ratio the user supplies via
    TDFAllocation. CASH is uninvested cash sitting in the account, which the
    rebalance engine always targets down to zero (i.e. "fully invest it")."""

    DOMESTIC_EQUITY = "domestic_equity"
    INTERNATIONAL_EQUITY = "international_equity"
    DOMESTIC_BOND = "domestic_bond"
    TDF = "tdf"
    CASH = "cash"


# Fund types that represent a single, directly tradeable fund with its own
# name/ticker (as opposed to CASH, which is not a security).
TRADEABLE_FUND_TYPES = (
    FundType.DOMESTIC_EQUITY,
    FundType.INTERNATIONAL_EQUITY,
    FundType.DOMESTIC_BOND,
    FundType.TDF,
)


class TaxTreatment(Enum):
    """Whether an account shelters gains/interest from current taxation.
    Roth/Traditional IRA & 401(k), HSA, etc. are TAX_ADVANTAGED; a regular
    brokerage account is TAXABLE."""

    TAX_ADVANTAGED = "tax_advantaged"
    TAXABLE = "taxable"


@dataclass(frozen=True)
class TDFAllocation:
    """The underlying stock/bond/region split of a target-date fund, as
    reported by the fund's fact sheet. Percentages must sum to ~100."""

    domestic_equity_pct: Decimal
    international_equity_pct: Decimal
    bond_pct: Decimal

    def __post_init__(self) -> None:
        for field_name in ("domestic_equity_pct", "international_equity_pct", "bond_pct"):
            value = getattr(self, field_name)
            if value < 0:
                raise ValueError(f"TDFAllocation.{field_name} cannot be negative: {value}")
        total = self.domestic_equity_pct + self.international_equity_pct + self.bond_pct
        if abs(total - Decimal(100)) > PERCENT_SUM_TOLERANCE:
            raise ValueError(
                "TDFAllocation percentages must sum to 100 "
                f"(got {total}: domestic={self.domestic_equity_pct}, "
                f"intl={self.international_equity_pct}, bond={self.bond_pct})"
            )


@dataclass(frozen=True)
class Holding:
    """A single fund (or cash) position within one account."""

    fund_type: FundType
    name: str
    balance: Decimal
    # Required if and only if fund_type is TDF.
    tdf_allocation: TDFAllocation | None = None

    def __post_init__(self) -> None:
        if self.balance < 0:
            raise ValueError(f"Holding balance cannot be negative: {self.balance}")
        if self.fund_type == FundType.TDF and self.tdf_allocation is None:
            raise ValueError("A TDF holding requires a tdf_allocation")
        if self.fund_type != FundType.TDF and self.tdf_allocation is not None:
            raise ValueError(f"Only TDF holdings may carry a tdf_allocation, got {self.fund_type}")
        if self.fund_type != FundType.CASH and not self.name.strip():
            raise ValueError("A fund holding must have a non-empty name/ticker")

    def domestic_equity_component(self) -> Decimal:
        """Dollar amount of this holding attributable to domestic equity."""
        if self.fund_type == FundType.DOMESTIC_EQUITY:
            return self.balance
        if self.fund_type == FundType.TDF:
            return self.balance * self.tdf_allocation.domestic_equity_pct / Decimal(100)
        return Decimal(0)

    def international_equity_component(self) -> Decimal:
        """Dollar amount of this holding attributable to international equity."""
        if self.fund_type == FundType.INTERNATIONAL_EQUITY:
            return self.balance
        if self.fund_type == FundType.TDF:
            return self.balance * self.tdf_allocation.international_equity_pct / Decimal(100)
        return Decimal(0)

    def bond_component(self) -> Decimal:
        """Dollar amount of this holding attributable to bonds."""
        if self.fund_type == FundType.DOMESTIC_BOND:
            return self.balance
        if self.fund_type == FundType.TDF:
            return self.balance * self.tdf_allocation.bond_pct / Decimal(100)
        return Decimal(0)


@dataclass
class Account:
    """One investment account: a 401(k), an IRA, a taxable brokerage account,
    etc. `name` is a user-chosen nickname that must be unique across the
    whole portfolio (this is how multiple accounts of the same type, e.g.
    two 401(k)s from different employers, are distinguished)."""

    account_type: str
    name: str
    tax_treatment: TaxTreatment
    holdings: list[Holding] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError("Account name cannot be empty")
        seen_types = set()
        for holding in self.holdings:
            if holding.fund_type in seen_types:
                raise ValueError(
                    f"Account {self.name!r} has more than one "
                    f"{holding.fund_type.value} holding; only one per fund type is supported"
                )
            seen_types.add(holding.fund_type)

    def total_value(self) -> Decimal:
        return sum((h.balance for h in self.holdings), Decimal(0))

    def get_holding(self, fund_type: FundType) -> Holding | None:
        return next((h for h in self.holdings if h.fund_type == fund_type), None)

    def cash_balance(self) -> Decimal:
        holding = self.get_holding(FundType.CASH)
        return holding.balance if holding else Decimal(0)

    def is_tax_advantaged(self) -> bool:
        return self.tax_treatment == TaxTreatment.TAX_ADVANTAGED


@dataclass(frozen=True)
class TargetAllocation:
    """The whole-portfolio target split, in percent, across the three fund
    types. Derived from a stock/bond target plus VT's US/ex-US weighting --
    see allocation.py."""

    domestic_equity_pct: Decimal
    international_equity_pct: Decimal
    bond_pct: Decimal

    def __post_init__(self) -> None:
        for field_name in ("domestic_equity_pct", "international_equity_pct", "bond_pct"):
            value = getattr(self, field_name)
            if value < 0:
                raise ValueError(f"TargetAllocation.{field_name} cannot be negative: {value}")
        total = self.domestic_equity_pct + self.international_equity_pct + self.bond_pct
        if abs(total - Decimal(100)) > PERCENT_SUM_TOLERANCE:
            raise ValueError(f"TargetAllocation percentages must sum to 100 (got {total})")


@dataclass(frozen=True)
class Trade:
    """One recommended buy or sell of a specific fund within a specific
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
