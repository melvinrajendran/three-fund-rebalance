"""Core data model for the three-fund rebalancer.

Everything in this module is a plain, immutable-ish dataclass with just enough
validation to keep the rest of the codebase (persistence, the LP engine,
reporting) from having to re-check basic invariants. All money is represented
as `Decimal`, never `float`, to avoid binary floating point rounding creeping
into dollar amounts a user might act on.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field, replace
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
    """The kinds of holdings a slot in an account can be. MULTI_ASSET is a
    single position that internally bundles U.S. stocks, international
    stocks, and bonds in a fixed ratio the user supplies via
    FundAllocation -- a target-date fund is one example, a balanced or
    lifestrategy fund another. CASH is cash sitting available in the account,
    which the rebalance engine always targets down to zero (i.e. "invest all
    of it")."""

    US_STOCK = "us_stock"
    INTERNATIONAL_STOCK = "international_stock"
    US_BOND = "us_bond"
    MULTI_ASSET = "multi_asset"
    CASH = "cash"


# The three asset classes held as a fund of their own. An account may hold
# any combination of these and of multi-asset funds -- see
# Account.__post_init__, which no longer keeps the two apart.
SINGLE_ASSET_FUND_TYPES = (
    FundType.US_STOCK,
    FundType.INTERNATIONAL_STOCK,
    FundType.US_BOND,
)

# Fund types that represent a single, directly tradeable fund with its own
# name/ticker (as opposed to CASH, which is not a security).
TRADEABLE_FUND_TYPES = (*SINGLE_ASSET_FUND_TYPES, FundType.MULTI_ASSET)


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
class FundAllocation:
    """The mix of U.S. stocks, international stocks, and bonds held inside a
    single multi-asset fund, as reported by the fund's fact sheet.
    Percentages must sum to ~100."""

    us_stock_pct: Decimal
    international_stock_pct: Decimal
    bond_pct: Decimal

    def __post_init__(self) -> None:
        for field_name in ("us_stock_pct", "international_stock_pct", "bond_pct"):
            value = getattr(self, field_name)
            if value < 0:
                raise ValueError(f"FundAllocation.{field_name} cannot be negative: {value}")
        total = self.us_stock_pct + self.international_stock_pct + self.bond_pct
        if abs(total - Decimal(100)) > PERCENT_SUM_TOLERANCE:
            raise ValueError(
                "FundAllocation percentages must sum to 100 "
                f"(got {total}: us_stock={self.us_stock_pct}, "
                f"international_stock={self.international_stock_pct}, bond={self.bond_pct})"
            )

    def percent_of(self, fund_type: FundType) -> Decimal:
        """This fund's share of one asset class as the user entered it, in
        percent -- 0 for anything that is not one of the three.

        The one place the three fields are mapped to the asset classes they
        stand for, which is why `fraction_of` below goes through it rather
        than repeating the mapping, and why the report asks for a sleeve by
        `FundType` instead of by attribute name.

        Exactly what was entered, unrounded and unnormalized: a fact sheet
        printing 34.34% is read back as 34.34%. `fraction_of` is the derived
        view the solver needs; this is the fund's own number, and everything
        that echoes a mix to the user prints this one.
        """
        return {
            FundType.US_STOCK: self.us_stock_pct,
            FundType.INTERNATIONAL_STOCK: self.international_stock_pct,
            FundType.US_BOND: self.bond_pct,
        }.get(fund_type, Decimal(0))

    def fraction_of(self, fund_type: FundType) -> Decimal:
        """This fund's share of one asset class, as a fraction of the whole
        position -- normalized so the three shares sum to exactly 1.

        Deliberately not `pct / 100`. The percentages come off a fact sheet
        and are only required to sum to 100 within PERCENT_SUM_TOLERANCE, so
        a fund printed as 64.0 / 34.3 / 1.6 sums to 99.9. Dividing by 100
        would leave a tenth of a percent of the position belonging to no
        asset class at all, which contradicts the plainer fact that every
        dollar in the fund is invested in something. The solver states both
        as hard equalities -- each account spends exactly its own total, each
        asset class hits exactly its resolved figure -- so the contradiction
        does not degrade an answer, it makes an ordinary portfolio
        infeasible. Dividing by the actual sum spreads the fact sheet's
        rounding across the three sleeves in proportion, which is the only
        reading that keeps the two statements consistent.

        The percentages themselves are stored exactly as entered; this is a
        derived view, so prompts and the report still echo the fund's own
        numbers back -- see `percent_of`, which this is built on so the three
        fields are mapped to their asset classes in one place.
        """
        # __post_init__ has already pinned the sum near 100, so this is never
        # a division by zero -- and a fund type that is not one of the three
        # divides 0 by it, which is the 0 it should be.
        return self.percent_of(fund_type) / (
            self.us_stock_pct + self.international_stock_pct + self.bond_pct
        )


@dataclass(frozen=True)
class Holding:
    """A single fund (or cash) position within one account."""

    fund_type: FundType
    name: str
    value: Decimal
    # Required if and only if fund_type is MULTI_ASSET.
    allocation: FundAllocation | None = None

    def __post_init__(self) -> None:
        if self.value < 0:
            raise ValueError(f"Holding value cannot be negative: {self.value}")
        if self.fund_type == FundType.MULTI_ASSET and self.allocation is None:
            raise ValueError("A multi-asset fund holding requires an allocation")
        if self.fund_type != FundType.MULTI_ASSET and self.allocation is not None:
            raise ValueError(
                f"Only multi-asset fund holdings may carry an allocation, got {self.fund_type}"
            )
        if self.fund_type != FundType.CASH and not self.name.strip():
            raise ValueError("A fund holding must have a non-empty name/ticker")

    def fraction_of(self, asset_class: FundType) -> Decimal:
        """How much of one dollar held here counts toward `asset_class`: 1 for
        a direct match, the fund's own internal share for a multi-asset fund,
        0 otherwise.

        The single statement of that rule. It used to be written out four
        times -- once per asset class here, and again as
        `rebalance._fund_type_coefficient` for the solver, which needs the
        same number as a float. Four copies of one invariant is three chances
        for them to disagree, and the solver reading a holding differently
        from the report is the exact disagreement that makes a portfolio
        infeasible rather than merely misreported.

        Named to match `FundAllocation.fraction_of`, which answers the same
        question one level down and which this delegates to.
        """
        if self.fund_type == asset_class:
            return Decimal(1)
        if self.fund_type == FundType.MULTI_ASSET:
            return self.allocation.fraction_of(asset_class)
        return Decimal(0)

    def component(self, asset_class: FundType) -> Decimal:
        """Dollar amount of this holding attributable to `asset_class`."""
        return self.value * self.fraction_of(asset_class)


def fund_name_key(name: str) -> str:
    """A fund name as every comparison of one reads it -- case and surrounding
    space are noise a user shouldn't have to get right. The key an order is
    placed against within an account, and the key a fund's details are kept
    under across the whole portfolio."""
    return name.strip().casefold()


@dataclass(frozen=True)
class FundProfile:
    """What a fund *is*, apart from how much of it any one account holds: its
    name, which asset class it holds, and its mix if that is several.

    A fund name maps to exactly one of these across every account. VBIAX in a
    Roth and VBIAX in a brokerage account are the same security, so they
    cannot hold different things -- and letting two copies of its details
    exist is what lets them come to disagree. Only the value is per account.
    """

    name: str
    fund_type: FundType
    allocation: FundAllocation | None = None

    def __post_init__(self) -> None:
        # Holding's own checks, which a profile has to pass to become one.
        self.holding(Decimal(0))
        if self.fund_type == FundType.CASH:
            raise ValueError("Cash is not a fund and has no saved details")

    @classmethod
    def of(cls, holding: Holding) -> FundProfile:
        return cls(name=holding.name, fund_type=holding.fund_type, allocation=holding.allocation)

    def holding(self, value: Decimal, *, name: str | None = None) -> Holding:
        """This fund held at `value`. `name` keeps an account's own spelling
        of it -- "vti" and "VTI" are one fund, and the order is placed against
        whichever the account was entered with."""
        return Holding(
            fund_type=self.fund_type,
            name=self.name if name is None else name,
            value=value,
            allocation=self.allocation,
        )

    def same_details(self, other: FundProfile) -> bool:
        """Whether two profiles describe a fund the same way -- spelling of
        the name aside, which is not a detail."""
        return self.fund_type == other.fund_type and self.allocation == other.allocation


class FundCatalog:
    """Every fund entered, one `FundProfile` per name.

    Kept in the config file so a fund typed again in another account is
    offered back with its details rather than described from scratch, and so
    the details exist in exactly one place. What is saved is only the funds
    some account still holds -- see `held_by`.
    """

    def __init__(
        self, profiles: Iterable[FundProfile] = (), accounts: Iterable[Account] = ()
    ) -> None:
        """Seeded from saved profiles and from the funds the accounts hold.
        Either may repeat a fund, but never with different details: that is
        two answers to one question, and picking one would silently change
        what some account holds."""
        self._by_key: dict[str, FundProfile] = {}
        seeds = [*profiles, *(FundProfile.of(h) for a in accounts for h in a.funds())]
        for profile in seeds:
            known = self.get(profile.name)
            if known is None:
                self.set(profile)
            elif not known.same_details(profile):
                raise ValueError(
                    f"Fund {profile.name!r} is described two different ways; "
                    "a fund's details must be the same in every account"
                )

    def get(self, name: str) -> FundProfile | None:
        return self._by_key.get(fund_name_key(name))

    def set(self, profile: FundProfile) -> None:
        """Record `profile` as what its fund is, replacing any earlier answer
        -- including the spelling of the name."""
        self._by_key[fund_name_key(profile.name)] = profile

    def profiles(self) -> list[FundProfile]:
        """In the order they were first entered."""
        return list(self._by_key.values())

    def held_by(self, accounts: Iterable[Account]) -> FundCatalog:
        """Only the funds some account holds, in the same order.

        A fund removed from every account is gone from the portfolio, so it is
        dropped from what is saved rather than kept forever. Within a run the
        full catalog still answers, so a fund moved from one account to
        another in the same session is still recognized.
        """
        held = {fund_name_key(h.name) for a in accounts for h in a.funds()}
        return FundCatalog(p for key, p in self._by_key.items() if key in held)

    def resolve(self, accounts: Iterable[Account]) -> list[Account]:
        """The accounts with every fund's details taken from here, each
        keeping its own value and its own spelling of the name.

        This is what makes a change to one fund a change in every account that
        holds it: the answer is recorded once, and every holding is re-read
        from it. A fund not in the catalog is left as it is.
        """
        resolved = []
        for account in accounts:
            holdings = []
            for holding in account.holdings:
                profile = (
                    self.get(holding.name) if holding.fund_type != FundType.CASH else None
                )
                holdings.append(
                    holding
                    if profile is None
                    else profile.holding(holding.value, name=holding.name)
                )
            resolved.append(replace(account, holdings=holdings))
        return resolved


@dataclass
class Account:
    """One investment account: a 401(k), an IRA, a taxable brokerage account,
    etc. `name` is a user-chosen nickname that must be unique across the
    whole portfolio (this is how multiple accounts of the same type, e.g.
    two 401(k)s from different employers, are distinguished).

    An account holds any combination of funds -- any number dedicated to a
    single asset class, any number of multi-asset funds, in any mix -- with
    cash alongside. What it may *not* hold is two funds under one name: that
    is the identity a trade is placed against, so two positions sharing it
    is a data-entry mistake rather than a lineup.
    """

    account_type: str
    name: str
    tax_treatment: TaxTreatment
    holdings: list[Holding] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError("Account name cannot be empty")

        # One cash balance, because `available_cash` reads a single holding
        # and two would be silently half-counted everywhere it is used.
        if sum(1 for h in self.holdings if h.fund_type == FundType.CASH) > 1:
            raise ValueError(f"Account {self.name!r} has more than one cash balance")

        # Fund names unique within the account, compared the way
        # `rebalance._normalized_fund_name` compares them -- case and
        # surrounding space are noise a user shouldn't have to get right.
        # This is the key an order is placed against, the key
        # `report.allocation_after_trades` applies a trade by, and the label
        # the report's own rows are read by; two holdings sharing it make
        # all three ambiguous at once.
        seen_names: set[str] = set()
        for holding in self.holdings:
            if holding.fund_type == FundType.CASH:
                continue
            key = fund_name_key(holding.name)
            if key in seen_names:
                raise ValueError(
                    f"Account {self.name!r} has more than one holding named "
                    f"{holding.name.strip()!r}; each fund in an account needs its own "
                    "name or ticker"
                )
            seen_names.add(key)

    def total_value(self) -> Decimal:
        return sum((h.value for h in self.holdings), Decimal(0))

    def funds(self) -> list[Holding]:
        """Everything in the account that is a security, in declared order.

        Cash is the one holding that is not: it has no name, no trade of its
        own, and an implicit target of zero. Three callers want exactly this
        list, and each used to filter for it inline.
        """
        return [h for h in self.holdings if h.fund_type != FundType.CASH]

    def get_holding(self, fund_type: FundType) -> Holding | None:
        """The *first* holding of this fund type, or None.

        An account may now hold several funds of one type, so this answers a
        question only CASH still has a single answer to. Anything wanting
        every fund of a type should iterate `funds()` itself.
        """
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
class Note:
    """One thing the reader has to be told about a plan, in two parts.

    `label` is a two- or three-word name for the finding, printed leading the
    summary, so a reader can decide from the first words whether the
    paragraph is theirs -- the tail of the report is where several unrelated
    findings pile up, and undifferentiated paragraphs are what made it a wall.
    `detail` is the part that explains or qualifies rather than reports;
    `report` sets it one level deeper, where it reads as optional. A note that
    says everything it has to say in one sentence leaves it None.

    Structured rather than pre-formatted prose because `report` owns how these
    land on the page, exactly as it owns every other line: the solver's job is
    to say what happened, not how wide it wraps or how far it indents.
    """

    label: str
    summary: str
    detail: str | None = None


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


@dataclass
class RebalanceResult:
    """What a rebalance came out to: the orders, the notes the user has to be
    told about them, and two figures the report discloses.

    Lives here rather than in `rebalance` so that `report` -- whose whole job
    is to render this -- depends on the data and not on the solver that
    produced it. `Trade` was already here; this was the one piece of the
    solver's output that still dragged scipy into the reporting layer.
    """

    trades: list[Trade]
    # Named for what the report prints them under. They are not all warnings
    # -- a taxable sale is a disclosure and a dropped order is a footnote --
    # and one heading over the lot is what lets the reader take them as a
    # single, countable list rather than a run of loose paragraphs.
    notes: list[Note]
    # Total $ of bonds left in taxable accounts in the final solution (0 if
    # tax-advantaged capacity was sufficient to hold the whole bond target).
    taxable_bond_dollars: Decimal
    # How many moves were wanted but left out for being smaller than
    # MIN_TRADE_DOLLARS. The report says so: with them dropped, the orders
    # shown do not reach the target exactly, and silence about that reads as
    # a rounding error nobody can account for.
    dropped_trades: int = 0
