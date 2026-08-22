"""Interactive input collection and validation.

Everything reads through a `Prompter`, a thin wrapper around input()/print(),
so the whole flow can be driven by a scripted list of canned responses in
tests without monkeypatching builtins.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from decimal import Decimal, InvalidOperation

from three_fund_rebalance.config import (
    ACCOUNT_TYPE_CHOICES,
    DEFAULT_REBALANCE_BAND_PCT,
    FALLBACK_VT_US_PCT,
    MAX_ACCOUNT_NAME_LENGTH,
    VT_FACT_SHEET_URL,
    infer_tax_treatment,
)
from three_fund_rebalance.formatting import (
    ASSET_CLASS_LABELS,
    INDENT_UNIT,
    format_account_heading,
    format_percent,
    format_subheading,
    prose_width,
    wrap,
)
from three_fund_rebalance.models import (
    INDIVIDUAL_FUND_TYPES,
    PERCENT_SUM_TOLERANCE,
    Account,
    FundType,
    Holding,
    TargetDateAllocation,
    TaxTreatment,
)
from three_fund_rebalance.vt_allocation import VTAllocationResult, VTFetchError, fetch_vt_us_pct

# (fund type, how the question names it) -- also the order the slots are asked in.
_INDIVIDUAL_SLOT_PROMPTS: list[tuple[FundType, str]] = [
    (FundType.US_STOCK, "a U.S. stock fund"),
    (FundType.INTERNATIONAL_STOCK, "an international stock fund"),
    (FundType.US_BOND, "a U.S. bond fund"),
]

# An account holds one or the other, never a mix, so this is asked once up
# front rather than as a fourth "does it hold..." question that could be
# answered yes alongside the others.
_INDIVIDUAL_FUNDS_CHOICE = "Individual funds (U.S. stock, international stock, bonds)"
_TARGET_DATE_CHOICE = "A single target-date fund"
_HOLDING_KIND_CHOICES = [_INDIVIDUAL_FUNDS_CHOICE, _TARGET_DATE_CHOICE]

# Asked only for an account type we don't recognize. Worded by when the tax
# is paid rather than by the category name, since "tax-deferred" versus
# "tax-free" is exactly the distinction someone picking "Other" may not have
# the vocabulary for -- and it is the one that decides where bonds go.
_TAX_TREATMENT_BY_CHOICE: dict[str, TaxTreatment] = {
    "Taxable -- I pay tax on dividends and gains every year": TaxTreatment.TAXABLE,
    "Tax-deferred -- contributions go in untaxed, withdrawals are taxed "
    "(traditional 401(k)/IRA)": TaxTreatment.TAX_DEFERRED,
    "Tax-free -- contributions are taxed, qualified withdrawals are not "
    "(Roth, HSA)": TaxTreatment.TAX_FREE,
}


class Prompter:
    def __init__(
        self,
        input_func: Callable[[str], str] = input,
        print_func: Callable[[str], None] = print,
    ):
        self._input = input_func
        self._print = print_func
        self._indent = ""

    @contextmanager
    def indented(self) -> Iterator[Prompter]:
        """Nest everything said or asked inside the block one level deeper.
        Depth is what shows structure below the ruled headings, so it lives
        on the prompter rather than being spelled into each message."""
        outer = self._indent
        self._indent += INDENT_UNIT
        try:
            yield self
        finally:
            self._indent = outer

    def _at_depth(self, text: str) -> str:
        """Indent every content line, leaving leading blank lines flush so a
        message that opens with a separator still gets one."""
        if not self._indent:
            return text
        blank_lead = len(text) - len(text.lstrip("\n"))
        body = text[blank_lead:]
        if not body:
            return text
        return "\n" * blank_lead + "\n".join(
            self._indent + line if line else line for line in body.split("\n")
        )

    def ask(self, text: str) -> str:
        return self._input(self._at_depth(text)).strip()

    def say(self, message: str = "") -> None:
        self._print(self._at_depth(message))

    def say_wrapped(self, message: str) -> None:
        """Say a paragraph reflowed to the page width *at the current depth*,
        so a note nested three levels deep still ends where every other line
        on screen does.

        Leading blank lines are kept rather than reflowed away: several
        messages open with one as a separator, exactly as `_at_depth`
        allows for.
        """
        blank_lead = len(message) - len(message.lstrip("\n"))
        body = message[blank_lead:]
        self.say("\n" * blank_lead + wrap(body, width=prose_width() - len(self._indent)))


# --------------------------------------------------------------------------
# Low-level primitives
# --------------------------------------------------------------------------


def prompt_str(
    prompter: Prompter, text: str, *, default: str | None = None, max_length: int | None = None
) -> str:
    suffix = f" [{default}]" if default else ""
    while True:
        raw = prompter.ask(f"{text}{suffix}: ")
        if not raw and default is not None:
            return default
        if raw and max_length is not None and len(raw) > max_length:
            prompter.say(f"Please keep this to {max_length} characters or fewer.")
            continue
        if raw:
            return raw
        prompter.say("This can\'t be empty -- please try again.")


def prompt_decimal(
    prompter: Prompter,
    text: str,
    *,
    default: Decimal | None = None,
    min_value: Decimal | None = None,
    max_value: Decimal | None = None,
    default_text: str | None = None,
) -> Decimal:
    shown = default if default_text is None else default_text
    suffix = f" [{shown}]" if default is not None else ""
    while True:
        raw = prompter.ask(f"{text}{suffix}: ")
        if not raw and default is not None:
            return default
        try:
            value = Decimal(raw)
        except InvalidOperation:
            prompter.say("Please enter a number.")
            continue
        if min_value is not None and value < min_value:
            prompter.say(f"Must be at least {min_value}.")
            continue
        if max_value is not None and value > max_value:
            prompter.say(f"Must be at most {max_value}.")
            continue
        return value


def prompt_percent(prompter: Prompter, text: str, *, default: Decimal | None = None) -> Decimal:
    """Ask for a percentage. One place for the 0-100 bounds, for the "(%)"
    the question carries, and for how a default is shown -- which is what
    stops one prompt offering [80] while the next offers [62.0]."""
    return prompt_decimal(
        prompter,
        f"{text} (%)",
        default=default,
        default_text=None if default is None else format_percent(default),
        min_value=Decimal(0),
        max_value=Decimal(100),
    )


def prompt_yes_no(prompter: Prompter, text: str, *, default: bool | None = None) -> bool:
    suffix = " [Y/n]" if default is True else " [y/N]" if default is False else " [y/n]"
    while True:
        raw = prompter.ask(f"{text}{suffix}: ").lower()
        if not raw and default is not None:
            return default
        if raw in ("y", "yes"):
            return True
        if raw in ("n", "no"):
            return False
        prompter.say("Please answer y or n.")


def prompt_choice(
    prompter: Prompter,
    text: str,
    choices: list[str],
    *,
    default: str | None = None,
    list_choices: bool = True,
) -> str:
    """Ask the user to pick from a numbered list.

    `list_choices=False` asks without reprinting the list, for a question
    asked repeatedly with the same options -- eleven account types reprinted
    for every account is most of the screen. "?" brings the list back, so
    nothing is ever unreachable from having been scrolled past.
    """
    # A leading newline in `text` is a separator from whatever came before,
    # and has to survive being folded into a one-line question.
    lead = "\n" * (len(text) - len(text.lstrip("\n")))
    label = text.strip().rstrip(":")

    # "Account type" wants a colon; "What does this account hold?" does not.
    heading = label if label.endswith(("?", ".")) else f"{label}:"

    def show_choices() -> None:
        prompter.say(f"{lead}{heading}")
        for i, choice in enumerate(choices, start=1):
            prompter.say(f"  {i}. {choice}")

    if list_choices:
        show_choices()

    default_index = choices.index(default) + 1 if default in choices else None
    suffix = f" [{default_index}]" if default_index else ""
    while True:
        if list_choices:
            raw = prompter.ask(f"Enter a number{suffix}: ")
        else:
            raw = prompter.ask(f"{lead}{label} (1-{len(choices)}, ? to list){suffix}: ")
        if not raw and default_index:
            return choices[default_index - 1]
        if raw.isdigit() and 1 <= int(raw) <= len(choices):
            return choices[int(raw) - 1]
        if raw == "?":
            show_choices()
            list_choices = True
            continue
        prompter.say(f"Please enter a number between 1 and {len(choices)}.")


# --------------------------------------------------------------------------
# Stock/bond target
# --------------------------------------------------------------------------


def prompt_stock_bond_allocation(
    prompter: Prompter,
    *,
    default_stock: Decimal | None = None,
    default_bond: Decimal | None = None,
) -> tuple[Decimal, Decimal]:
    while True:
        stock = prompt_percent(prompter, "Target stock allocation", default=default_stock)
        bond = prompt_percent(prompter, "Target bond allocation", default=default_bond)
        total = stock + bond
        if abs(total - Decimal(100)) <= PERCENT_SUM_TOLERANCE:
            return stock, bond
        prompter.say(f"Stock % and bond % must sum to 100 (got {total}). Let's try again.")


# --------------------------------------------------------------------------
# Rebalancing band
# --------------------------------------------------------------------------


def prompt_rebalance_band(prompter: Prompter, *, default: Decimal | None = None) -> Decimal:
    """How far an asset class may drift before it's worth correcting.

    Asked bare, like every other question in the flow: the band is explained
    where its effect is visible, in the report's own "Rebalancing band"
    section, rather than in a wall of preamble the user reads once and then
    scrolls past on every subsequent run.
    """
    return prompt_percent(
        prompter,
        "Rebalance when an asset class is off target by more than",
        default=default if default is not None else DEFAULT_REBALANCE_BAND_PCT,
    )


# --------------------------------------------------------------------------
# VT's U.S. allocation, with the live-fetch -> cache -> manual fallback chain
# --------------------------------------------------------------------------


def resolve_vt_allocation(
    prompter: Prompter,
    *,
    cached_us_pct: Decimal | None = None,
    cached_as_of: str | None = None,
    offline: bool = False,
) -> VTAllocationResult:
    # Each fallback below separates itself from whatever came before it, but
    # the first one to speak sits flush under the subheading cli.py printed.
    spoken = False

    if not offline:
        prompter.say("Looking up VT's current U.S./international stock allocation from Vanguard...")
        spoken = True
        try:
            result = fetch_vt_us_pct()
            prompter.say(
                f"  Found: {format_percent(result.us_pct)}% U.S. / "
                f"{format_percent(result.ex_us_pct)}% international "
                f"(as of {result.as_of})."
            )
            if prompt_yes_no(prompter, "  Use this value?", default=True):
                return result
        except VTFetchError as exc:
            with prompter.indented():
                prompter.say_wrapped(f"Couldn't look up the current allocation ({exc}).")

    if cached_us_pct is not None:
        lead = "\n" if spoken else ""
        prompter.say(
            f"{lead}Last known value: {format_percent(cached_us_pct)}% U.S. "
            f"(as of {cached_as_of or 'unknown date'})."
        )
        spoken = True
        if prompt_yes_no(prompter, "  Use this cached value?", default=True):
            return VTAllocationResult(us_pct=cached_us_pct, as_of=cached_as_of or "unknown date", source="cache")

    suggested_default = cached_us_pct if cached_us_pct is not None else FALLBACK_VT_US_PCT
    lead = "\n" if spoken else ""
    prompter.say_wrapped(
        f"{lead}Please enter VT's U.S. stock allocation % manually "
        f"(see {VT_FACT_SHEET_URL} or Vanguard's fund page)."
    )
    manual = prompt_percent(prompter, "U.S. stock", default=suggested_default)
    return VTAllocationResult(us_pct=manual, as_of="manually entered", source="manual")


# --------------------------------------------------------------------------
# Accounts
# --------------------------------------------------------------------------


def _describe_target_date_allocation(allocation: TargetDateAllocation) -> str:
    """The fund's own mix on one line. Shown before offering to change it, so
    the user is deciding against the numbers rather than from memory."""
    return (
        f"{format_percent(allocation.us_stock_pct)}% U.S. stocks / "
        f"{format_percent(allocation.international_stock_pct)}% international stocks / "
        f"{format_percent(allocation.bond_pct)}% bonds"
    )


def _prompt_target_date_allocation(prompter: Prompter) -> TargetDateAllocation:
    prompter.say("Enter this fund's underlying allocation (from its fact sheet):")
    with prompter.indented():
        while True:
            us_stock = prompt_percent(prompter, "U.S. stock")
            international = prompt_percent(prompter, "International stock")
            bond = prompt_percent(prompter, "Bond")
            total = us_stock + international + bond
            if abs(total - Decimal(100)) <= PERCENT_SUM_TOLERANCE:
                return TargetDateAllocation(
                    us_stock_pct=us_stock, international_stock_pct=international, bond_pct=bond
                )
            prompter.say(f"These must sum to 100 (got {total}). Let's try again.")


def _prompt_new_holding(prompter: Prompter, fund_type: FundType) -> Holding:
    # Depth comes from the prompter, never from spaces baked into the prompt
    # text: this helper is called from two places at different depths, and a
    # literal indent that lines up in one lands two levels off in the other.
    with prompter.indented():
        name = prompt_str(prompter, "Fund name or ticker symbol")
        value = prompt_decimal(prompter, "Current value ($)", default=Decimal(0), min_value=Decimal(0))
        target_date_allocation = (
            _prompt_target_date_allocation(prompter) if fund_type == FundType.TARGET_DATE else None
        )
    return Holding(fund_type=fund_type, name=name, value=value, target_date_allocation=target_date_allocation)


def _prompt_cash(prompter: Prompter, *, default: Decimal = Decimal(0)) -> Holding | None:
    cash = prompt_decimal(
        prompter, "Cash available to invest ($)", default=default, min_value=Decimal(0)
    )
    return Holding(fund_type=FundType.CASH, name="", value=cash) if cash > 0 else None


def _prompt_fund_holdings(prompter: Prompter) -> list[Holding]:
    """The funds an account holds -- one target-date fund, or any combination
    of the three individual funds. Never both; see Account's validation."""
    kind = prompt_choice(
        prompter,
        "What does this account hold?",
        _HOLDING_KIND_CHOICES,
        default=_INDIVIDUAL_FUNDS_CHOICE,
    )
    if kind == _TARGET_DATE_CHOICE:
        return [_prompt_new_holding(prompter, FundType.TARGET_DATE)]

    holdings = []
    for fund_type, description in _INDIVIDUAL_SLOT_PROMPTS:
        if prompt_yes_no(prompter, f"Does this account hold {description}?", default=False):
            holdings.append(_prompt_new_holding(prompter, fund_type))
    return holdings


def _prompt_holdings(prompter: Prompter, tax_treatment: TaxTreatment) -> list[Holding]:
    holdings = _prompt_fund_holdings(prompter)

    # Said here rather than after the cash question: it is about the answer
    # the user has just given, and three prompts later it reads as a non
    # sequitur.
    has_bonds = any(h.fund_type in (FundType.US_BOND, FundType.TARGET_DATE) for h in holdings)
    if tax_treatment == TaxTreatment.TAXABLE and has_bonds:
        prompter.say_wrapped(
            "Note: bonds in a taxable account (including inside a target-date fund) pay "
            "interest taxed as income each year. Where there is room, the rebalance holds "
            "bonds in your tax-advantaged accounts, preferring tax-deferred ones -- a "
            "common asset-location convention, not a prediction."
        )

    cash_holding = _prompt_cash(prompter)
    if cash_holding:
        holdings.append(cash_holding)
    return holdings


def _prompt_new_account(
    prompter: Prompter, existing_names: set[str], *, list_account_types: bool = True
) -> Account:
    account_type = prompt_choice(
        prompter, "\nAccount type", ACCOUNT_TYPE_CHOICES, list_choices=list_account_types
    )
    tax_treatment = infer_tax_treatment(account_type)
    if tax_treatment is None:
        tax_treatment = _TAX_TREATMENT_BY_CHOICE[
            prompt_choice(
                prompter, "How is this account taxed?", list(_TAX_TREATMENT_BY_CHOICE)
            )
        ]

    while True:
        name = prompt_str(
            prompter,
            "Account nickname (must be unique, e.g. 'Fidelity Roth IRA')",
            max_length=MAX_ACCOUNT_NAME_LENGTH,
        )
        if name in existing_names:
            prompter.say(f"'{name}' is already used -- please choose a different nickname.")
            continue
        break

    prompter.say("")
    with prompter.indented():
        prompter.say(format_account_heading(name, account_type))
        with prompter.indented():
            holdings = _prompt_holdings(prompter, tax_treatment)
    return Account(account_type=account_type, name=name, tax_treatment=tax_treatment, holdings=holdings)


def _prompt_update_existing_account(prompter: Prompter, existing: Account) -> Account:
    prompter.say("Press Enter to keep the last value.")
    new_holdings = []
    with prompter.indented():
        for holding in existing.holdings:
            if holding.fund_type == FundType.CASH:
                continue
            prompter.say(f"{holding.name} ({ASSET_CLASS_LABELS[holding.fund_type]} fund)")
            with prompter.indented():
                value = prompt_decimal(
                    prompter, "Current value ($)", default=holding.value, min_value=Decimal(0)
                )
                target_date_allocation = holding.target_date_allocation
                if holding.fund_type == FundType.TARGET_DATE:
                    prompter.say_wrapped(
                        f"Currently {_describe_target_date_allocation(target_date_allocation)}"
                    )
                    if prompt_yes_no(
                        prompter, "Update this fund's underlying allocation?", default=False
                    ):
                        target_date_allocation = _prompt_target_date_allocation(prompter)
            new_holdings.append(
                Holding(
                    fund_type=holding.fund_type,
                    name=holding.name,
                    value=value,
                    target_date_allocation=target_date_allocation,
                )
            )

    # What can be added depends on which kind of account this already is: a
    # target-date account is full at one fund, an individual-fund account can
    # gain the asset classes it's missing, and one holding only cash hasn't
    # committed to either yet.
    declared_types = {h.fund_type for h in existing.holdings}
    if FundType.TARGET_DATE in declared_types:
        pass
    elif declared_types & set(INDIVIDUAL_FUND_TYPES):
        for fund_type, description in _INDIVIDUAL_SLOT_PROMPTS:
            if fund_type in declared_types:
                continue
            if prompt_yes_no(prompter, f"Add {description} to this account?", default=False):
                new_holdings.append(_prompt_new_holding(prompter, fund_type))
    else:
        new_holdings.extend(_prompt_fund_holdings(prompter))

    cash_holding = _prompt_cash(prompter, default=existing.available_cash())
    if cash_holding:
        new_holdings.append(cash_holding)

    return Account(
        account_type=existing.account_type,
        name=existing.name,
        tax_treatment=existing.tax_treatment,
        holdings=new_holdings,
    )


def prompt_accounts(prompter: Prompter, existing_accounts: list[Account]) -> list[Account]:
    accounts: list[Account] = []
    if existing_accounts:
        prompter.say("\n" + format_subheading("Saved accounts"))
        noun = "account" if len(existing_accounts) == 1 else "accounts"
        prompter.say_wrapped(
            f"You have {len(existing_accounts)} saved {noun}: "
            f"{', '.join(a.name for a in existing_accounts)}"
        )
        for existing in existing_accounts:
            prompter.say("")
            with prompter.indented():
                prompter.say(format_account_heading(existing.name, existing.account_type))
                with prompter.indented():
                    if prompt_yes_no(prompter, "Keep this account?", default=True):
                        accounts.append(_prompt_update_existing_account(prompter, existing))
                    else:
                        prompter.say(f"Removed '{existing.name}'.")

    # Both states of this heading are imperative: it is a section that asks
    # you to do something, unlike "Saved accounts" above, which names a list.
    heading = "Add more accounts" if existing_accounts else "Add your accounts"
    prompter.say("\n" + format_subheading(heading))
    is_first_prompt = True
    listed_account_types = False
    while True:
        label = "Add an account?" if not accounts else "Add another account?"
        # Only the first question sits directly under the subheading; later
        # ones need a blank line to separate them from the account above.
        if not prompt_yes_no(prompter, label if is_first_prompt else f"\n{label}", default=not accounts):
            break
        # The eleven account types are worth seeing once. Reprinting them for
        # every account is most of the screen, so later ones ask in one line.
        accounts.append(
            _prompt_new_account(
                prompter,
                existing_names={a.name for a in accounts},
                list_account_types=not listed_account_types,
            )
        )
        listed_account_types = True
        is_first_prompt = False

    return accounts
