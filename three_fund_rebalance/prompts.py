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
    FALLBACK_VT_US_PCT,
    VT_FACT_SHEET_URL,
    infer_tax_treatment,
)
from three_fund_rebalance.formatting import (
    INDENT_UNIT,
    format_account_heading,
    format_subheading,
)
from three_fund_rebalance.models import (
    PERCENT_SUM_TOLERANCE,
    Account,
    FundType,
    Holding,
    TaxTreatment,
    TDFAllocation,
)
from three_fund_rebalance.vt_allocation import VTAllocationResult, VTFetchError, fetch_vt_us_pct

# (fund type, human description, whether it's a security that needs a name/ticker)
_SLOT_PROMPTS: list[tuple[FundType, str]] = [
    (FundType.DOMESTIC_EQUITY, "a domestic (US) equity fund"),
    (FundType.INTERNATIONAL_EQUITY, "an international (ex-US) equity fund"),
    (FundType.DOMESTIC_BOND, "a domestic bond fund"),
    (FundType.TDF, "a target-date fund (TDF)"),
]


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


# --------------------------------------------------------------------------
# Low-level primitives
# --------------------------------------------------------------------------


def prompt_str(prompter: Prompter, text: str, *, default: str | None = None) -> str:
    suffix = f" [{default}]" if default else ""
    while True:
        raw = prompter.ask(f"{text}{suffix}: ")
        if not raw and default is not None:
            return default
        if raw:
            return raw
        prompter.say("This can't be empty -- please try again.")


def prompt_decimal(
    prompter: Prompter,
    text: str,
    *,
    default: Decimal | None = None,
    min_value: Decimal | None = None,
    max_value: Decimal | None = None,
) -> Decimal:
    suffix = f" [{default}]" if default is not None else ""
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
    prompter: Prompter, text: str, choices: list[str], *, default: str | None = None
) -> str:
    prompter.say(text)
    for i, choice in enumerate(choices, start=1):
        prompter.say(f"  {i}. {choice}")
    default_index = choices.index(default) + 1 if default in choices else None
    suffix = f" [{default_index}]" if default_index else ""
    while True:
        raw = prompter.ask(f"Enter a number{suffix}: ")
        if not raw and default_index:
            return choices[default_index - 1]
        if raw.isdigit() and 1 <= int(raw) <= len(choices):
            return choices[int(raw) - 1]
        prompter.say(f"Please enter a number between 1 and {len(choices)}.")


# --------------------------------------------------------------------------
# Stock/bond target
# --------------------------------------------------------------------------


def prompt_stock_bond_target(
    prompter: Prompter,
    *,
    default_stock: Decimal | None = None,
    default_bond: Decimal | None = None,
) -> tuple[Decimal, Decimal]:
    while True:
        stock = prompt_decimal(
            prompter, "Target stock %", default=default_stock, min_value=Decimal(0), max_value=Decimal(100)
        )
        bond = prompt_decimal(
            prompter, "Target bond %", default=default_bond, min_value=Decimal(0), max_value=Decimal(100)
        )
        total = stock + bond
        if abs(total - Decimal(100)) <= PERCENT_SUM_TOLERANCE:
            return stock, bond
        prompter.say(f"Stock % and bond % must sum to 100 (got {total}). Let's try again.")


# --------------------------------------------------------------------------
# VT US/ex-US split, with the live-fetch -> cache -> manual fallback chain
# --------------------------------------------------------------------------


def resolve_vt_split(
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
        prompter.say("Fetching VT's current US/international stock weighting from Vanguard...")
        spoken = True
        try:
            result = fetch_vt_us_pct()
            prompter.say(
                f"  Found: {result.us_pct}% US / {result.ex_us_pct}% international "
                f"(as of {result.as_of})."
            )
            if prompt_yes_no(prompter, "  Use this value?", default=True):
                return result
        except VTFetchError as exc:
            prompter.say(f"  Could not fetch live data ({exc}).")

    if cached_us_pct is not None:
        lead = "\n" if spoken else ""
        prompter.say(
            f"{lead}Last known value: {cached_us_pct}% US "
            f"(as of {cached_as_of or 'unknown date'})."
        )
        spoken = True
        if prompt_yes_no(prompter, "  Use this cached value?", default=True):
            return VTAllocationResult(us_pct=cached_us_pct, as_of=cached_as_of or "unknown date", source="cache")

    suggested_default = cached_us_pct if cached_us_pct is not None else FALLBACK_VT_US_PCT
    lead = "\n" if spoken else ""
    prompter.say(
        f"{lead}Please enter VT's US stock allocation % manually "
        f"(see {VT_FACT_SHEET_URL} or Vanguard's fund page)."
    )
    manual = prompt_decimal(
        prompter, "US stock %", default=suggested_default, min_value=Decimal(0), max_value=Decimal(100)
    )
    return VTAllocationResult(us_pct=manual, as_of="manually entered", source="manual")


# --------------------------------------------------------------------------
# Accounts
# --------------------------------------------------------------------------


def _prompt_tdf_allocation(prompter: Prompter) -> TDFAllocation:
    prompter.say("    Enter this TDF's underlying allocation (from its fact sheet):")
    while True:
        domestic = prompt_decimal(
            prompter, "      Domestic equity %", min_value=Decimal(0), max_value=Decimal(100)
        )
        intl = prompt_decimal(
            prompter, "      International equity %", min_value=Decimal(0), max_value=Decimal(100)
        )
        bond = prompt_decimal(prompter, "      Bond %", min_value=Decimal(0), max_value=Decimal(100))
        total = domestic + intl + bond
        if abs(total - Decimal(100)) <= PERCENT_SUM_TOLERANCE:
            return TDFAllocation(
                domestic_equity_pct=domestic, international_equity_pct=intl, bond_pct=bond
            )
        prompter.say(f"    These must sum to 100 (got {total}). Let's try again.")


def _prompt_new_holding(prompter: Prompter, fund_type: FundType) -> Holding:
    name = prompt_str(prompter, "  Fund name/ticker")
    balance = prompt_decimal(prompter, "  Current balance ($)", default=Decimal(0), min_value=Decimal(0))
    tdf_allocation = _prompt_tdf_allocation(prompter) if fund_type == FundType.TDF else None
    return Holding(fund_type=fund_type, name=name, balance=balance, tdf_allocation=tdf_allocation)


def _prompt_cash(prompter: Prompter, *, default: Decimal = Decimal(0)) -> Holding | None:
    cash = prompt_decimal(
        prompter, "Uninvested cash in this account ($)", default=default, min_value=Decimal(0)
    )
    return Holding(fund_type=FundType.CASH, name="", balance=cash) if cash > 0 else None


def _prompt_holdings(prompter: Prompter, tax_treatment: TaxTreatment) -> list[Holding]:
    holdings = []
    for fund_type, description in _SLOT_PROMPTS:
        if prompt_yes_no(prompter, f"Does this account hold {description}?", default=False):
            holdings.append(_prompt_new_holding(prompter, fund_type))
    cash_holding = _prompt_cash(prompter)
    if cash_holding:
        holdings.append(cash_holding)

    has_bonds = any(h.fund_type in (FundType.DOMESTIC_BOND, FundType.TDF) for h in holdings)
    if tax_treatment == TaxTreatment.TAXABLE and has_bonds:
        prompter.say(
            "  Note: bonds (including via a TDF) held in a taxable account can trigger extra "
            "tax on interest income; the rebalance will try to minimize this where possible."
        )
    return holdings


def _prompt_new_account(prompter: Prompter, existing_names: set[str]) -> Account:
    account_type = prompt_choice(prompter, "\nAccount type:", ACCOUNT_TYPE_CHOICES)
    tax_treatment = infer_tax_treatment(account_type)
    if tax_treatment is None:
        tax_treatment = (
            TaxTreatment.TAX_ADVANTAGED
            if prompt_yes_no(
                prompter,
                "Is this account tax-advantaged (tax-deferred or tax-free growth), "
                "as opposed to a taxable brokerage account?",
                default=True,
            )
            else TaxTreatment.TAXABLE
        )

    while True:
        name = prompt_str(prompter, "Account nickname (must be unique, e.g. 'Fidelity Roth IRA')")
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
    for holding in existing.holdings:
        if holding.fund_type == FundType.CASH:
            continue
        prompter.say(f"  {holding.name} ({holding.fund_type.value.replace('_', ' ')})")
        balance = prompt_decimal(
            prompter, "    Current balance ($)", default=holding.balance, min_value=Decimal(0)
        )
        tdf_allocation = holding.tdf_allocation
        if holding.fund_type == FundType.TDF and prompt_yes_no(
            prompter, "    Update this TDF's underlying allocation?", default=False
        ):
            tdf_allocation = _prompt_tdf_allocation(prompter)
        new_holdings.append(
            Holding(
                fund_type=holding.fund_type,
                name=holding.name,
                balance=balance,
                tdf_allocation=tdf_allocation,
            )
        )

    declared_types = {h.fund_type for h in existing.holdings}
    for fund_type, description in _SLOT_PROMPTS:
        if fund_type in declared_types and fund_type != FundType.CASH:
            continue
        if fund_type not in declared_types and prompt_yes_no(
            prompter, f"Add {description} to this account?", default=False
        ):
            new_holdings.append(_prompt_new_holding(prompter, fund_type))

    cash_holding = _prompt_cash(prompter, default=existing.cash_balance())
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
        prompter.say(
            f"You have {len(existing_accounts)} saved account(s): "
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

    prompter.say("\n" + format_subheading("New accounts"))
    is_first_prompt = True
    while True:
        label = "Add an account?" if not accounts else "Add another account?"
        # Only the first question sits directly under the subheading; later
        # ones need a blank line to separate them from the account above.
        if not prompt_yes_no(prompter, label if is_first_prompt else f"\n{label}", default=not accounts):
            break
        accounts.append(_prompt_new_account(prompter, existing_names={a.name for a in accounts}))
        is_first_prompt = False

    return accounts
