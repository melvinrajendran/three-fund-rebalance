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
    MAX_ACCOUNT_NAME_LENGTH,
    VT_FUND_NAME,
    VT_FUND_PAGE_URL,
    VT_TICKER,
    infer_tax_treatment,
)
from three_fund_rebalance.formatting import (
    INDENT_UNIT,
    describe_as_of,
    format_account_heading,
    format_percent,
    format_subheading,
    prose_width,
    wrap,
)
from three_fund_rebalance.models import (
    Account,
    FundType,
    Holding,
    TargetDateAllocation,
    TaxTreatment,
)
from three_fund_rebalance.vt_allocation import VTAllocationResult, VTFetchError, fetch_vt_us_pct

# (fund type, what the question calls the slot) -- also the order the slots
# are asked in, and the order they end up in on the account.
#
# Singular before "fund", because an attributive noun does not pluralize: a
# bond fund, the way it is a shoe store. The asset classes standing on their
# own are plural everywhere else -- see _HOLDING_KIND_CHOICES below, the
# target-date allocation questions, and report._CATEGORY_LABELS.
_INDIVIDUAL_SLOT_PROMPTS: list[tuple[FundType, str]] = [
    (FundType.US_STOCK, "U.S. stock fund"),
    (FundType.INTERNATIONAL_STOCK, "International stock fund"),
    (FundType.US_BOND, "Bond fund"),
]

_TARGET_DATE_LABEL = "Target-date fund"

# An account holds one or the other, never a mix, so this is asked once up
# front rather than as a fourth "does it hold..." question that could be
# answered yes alongside the others.
#
# "Three" against "a single" is the contrast the question actually draws, and
# it is the honest one: choosing individual funds collects all three slots,
# so a label reading "Individual funds" would promise a menu that is not
# offered.
_INDIVIDUAL_FUNDS_CHOICE = "Three individual funds (U.S. stocks, international stocks, bonds)"
_TARGET_DATE_CHOICE = "A single target-date fund"
_HOLDING_KIND_CHOICES = [_INDIVIDUAL_FUNDS_CHOICE, _TARGET_DATE_CHOICE]

#: Said once, above the three fund questions. The flow otherwise asks bare
#: questions and lets the report explain, and the labels below do carry their
#: own meaning -- but nothing in "Bond fund:" says that a fund you own none of
#: still belongs in the answer, and that is the whole point of asking for all
#: three. One sentence for what to type, one granting permission to enter a
#: fund not yet bought: the failure mode here is not confusion but hesitation,
#: and "is fine" answers that in two words.
#:
#: "Position" is what every brokerage calls a line item, and "asset class" is
#: the vocabulary the band prompt and the report already use.
FUND_EXPLANATION = "Enter a name or ticker for each asset class. A $0 position is fine."

# Asked only for an account type we don't recognize. Worded by when the tax
# is paid rather than by the category name, since "tax-deferred" versus
# "tax-free" is exactly the distinction someone picking "Other" may not have
# the vocabulary for -- and it is the one that decides where bonds go.
_TAX_TREATMENT_BY_CHOICE: dict[str, TaxTreatment] = {
    "Taxable -- dividends taxed each year, gains taxed when I sell": TaxTreatment.TAXABLE,
    "Tax-deferred -- pre-tax now, taxed on withdrawal "
    "(traditional 401(k)/IRA)": TaxTreatment.TAX_DEFERRED,
    "Tax-free -- after-tax now, qualified withdrawals untaxed "
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


def _parses_as_a_number(raw: str) -> bool:
    """Whether `prompt_decimal` would have accepted this answer.

    Stated as "what the other question takes" rather than as a pattern of
    digits, so the two stay in step -- `prompt_decimal` parses with a bare
    `Decimal`, which is why a value typed with a comma or a dollar sign is
    not one of these and never reaches the name prompt in the first place.
    """
    try:
        Decimal(raw)
    except InvalidOperation:
        return False
    return True


def prompt_str(
    prompter: Prompter,
    text: str,
    *,
    default: str | None = None,
    max_length: int | None = None,
    reject_numeric: bool = False,
) -> str:
    """Ask for a line of text.

    `reject_numeric` guards the one pair of adjacent questions that take
    different kinds of answer. A fund's name is asked immediately above its
    value, and on a saved account the name arrives pre-filled while the value
    is the only thing that changed quarter to quarter -- so typing the new
    value at the name prompt is the natural slip, and nothing else here
    catches it. The amount becomes the fund's name, is saved to the config
    file, and comes back in the plan as "Buy $29,500.00 of 178000". No fund
    name or ticker is a bare number, so refusing one costs nothing.
    """
    suffix = f" [{default}]" if default else ""
    while True:
        raw = prompter.ask(f"{text}{suffix}: ")
        if not raw and default is not None:
            return default
        if raw and max_length is not None and len(raw) > max_length:
            prompter.say(f"Please keep this to {max_length} characters or fewer.")
            continue
        if raw and reject_numeric and _parses_as_a_number(raw):
            # "a fund name or ticker" is what FUND_EXPLANATION has already
            # asked for, so the correction reads as the same instruction
            # again -- and naming the fund is what says which of the two
            # adjacent questions this one is.
            prompter.say(f"'{raw}' looks like a number -- please enter a fund name or ticker.")
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


def prompt_percent(
    prompter: Prompter, text: str, *, default: Decimal | None = None, unit: str = "%"
) -> Decimal:
    """Ask for a percentage. One place for the 0-100 bounds, for the unit the
    question carries, and for how a default is shown -- which is what stops
    one prompt offering [80] while the next offers [62.0].

    `unit` is "%" for a share of something and "pts" for a distance between
    two percentages -- the same split the report keeps. The band's two halves
    are the one pair of questions where both appear side by side, and asking
    for "5 (%)" of drift next to "25 (%)" of a target is exactly how they get
    read as the same kind of number.
    """
    return prompt_decimal(
        prompter,
        f"{text} ({unit})",
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


def _and_list(items: list[str]) -> str:
    """Join names as running prose rather than as a bare comma list, so the
    line they sit in is a sentence: "A", "A and B", "A, B, and C".

    Two items take no comma -- the serial comma separates three or more, and
    "A, and B" reads as a stray one.
    """
    if len(items) <= 2:
        return " and ".join(items)
    return f"{', '.join(items[:-1])}, and {items[-1]}"


def _confirm_remainder(prompter: Prompter, statement: str, count: int) -> bool:
    """Confirm the share (or shares) a set of percentages summing to 100 has
    left over. One question shape for every such set, and the noun agrees
    with how many values are actually being shown -- one derived share is a
    value, two are values."""
    noun = "this value" if count == 1 else "these values"
    return prompt_yes_no(prompter, f"{statement} Use {noun}?", default=True)


def prompt_stock_bond_allocation(
    prompter: Prompter, *, default_stock: Decimal | None = None
) -> tuple[Decimal, Decimal]:
    """Ask for the stock share and derive the bond share from it.

    Percentages that must sum to 100 carry one fewer degree of freedom than
    they have questions, so asking for every one of them invites an answer
    that cannot be honored and turns a typo into a form the user has to
    re-fill. Ask for all but the last, state the last, and confirm it. A
    denial restarts from the first question, because the number the user
    wants to change is one they typed -- the derived one is not theirs to
    edit.
    """
    while True:
        stock = prompt_percent(prompter, "Target stock allocation", default=default_stock)
        bond = Decimal(100) - stock
        statement = f"That leaves a target bond allocation of {format_percent(bond)}%."
        if _confirm_remainder(prompter, statement, 1):
            return stock, bond


# --------------------------------------------------------------------------
# Rebalancing band
# --------------------------------------------------------------------------


#: Said once, above both halves of the band. The flow otherwise asks bare
#: questions and lets the report explain, but the relative half is the one
#: question nobody can answer from its own label: "or by more than this
#: percentage of its target" reads as an alternative when it is a second,
#: tighter limit, and the reason it exists -- that five points of drift is the
#: whole of a 5% bond sleeve -- is invisible from the prompt.
#:
#: Worded the way a rebalancing policy is written in an investment policy
#: statement -- a class "drifts from its target" by more than "the smaller
#: of" two bands -- so the sentence carries all the semantics and each
#: question below is left to name only its own unit.
#:
#: The vocabulary is the one the Bogleheads wiki and Larry Swedroe use, since
#: that is where a reader who wants to check what to answer will end up:
#: "rebalancing band", "asset class", an asset class that "drifts from" its
#: target. See config.DEFAULT_REBALANCE_BAND_PCT for the wiki link and for
#: the 5/25 rule, which this program records but never suggests.
#:
#: One sentence. Earlier drafts also named the rule, said what the relative
#: band is for, and noted that zero turns the band off -- all true, and all
#: cut back to the thing a reader needs in order to answer the two questions
#: below it. The report's own "Rebalancing Bands" section is where the band's
#: effect is visible, and it writes out the resulting ranges per class.
BAND_EXPLANATION = (
    "Rebalance the portfolio when an asset class drifts from its target by more than "
    "the smaller of these two bands."
)


def prompt_rebalance_band(prompter: Prompter, *, default: Decimal | None = None) -> Decimal:
    """The absolute band: percentage points of the whole portfolio.

    "Absolute band" and "relative band" are the industry's own names for the
    two halves, and the same words `rebalance_band_pct` and
    `rebalance_relative_band_pct` are named after. With BAND_EXPLANATION
    above them they read as two settings of one policy; each question then
    only has to say which unit it wants, which is the part that was actually
    ambiguous.

    **There is no suggested default.** `default` carries a saved answer and
    nothing else, so on a first run both halves have to be typed -- see
    prompt_relative_rebalance_band for why.
    """
    return prompt_percent(
        prompter,
        "Absolute band, in percentage points of the portfolio",
        default=default,
        unit="pts",
    )


def prompt_relative_rebalance_band(
    prompter: Prompter, *, default: Decimal | None = None
) -> Decimal:
    """The relative band: a share of the class's own target, so it scales
    with the target where the absolute band does not.

    Like the absolute half, this offers a saved answer and never a suggested
    one. The pair used to default to the 5/25 convention, which meant a first
    run could be walked past with two keystrokes -- and the band is the one
    setting here that decides whether the program does anything at all. A
    number the user did not choose, sitting under a heading that says
    "Rebalancing band", reads in the report as their own policy. Neither
    figure is universal: 5 and 25 are a convention, not a recommendation this
    program is in a position to make.

    The constants remain in `config.py` as the documented convention -- what
    is gone is the program answering the question on the user's behalf. The
    README does not name the rule either: a specific pair of numbers offered
    to a reader as the convention is the same suggestion by another route.
    """
    return prompt_percent(
        prompter,
        "Relative band, as a percentage of the asset class's target",
        default=default,
    )


# --------------------------------------------------------------------------
# VT's U.S. allocation, with the live-fetch -> cache -> manual fallback chain
# --------------------------------------------------------------------------


def _describe_us_ex_us(us_pct: Decimal, as_of: str) -> str:
    """The pair of figures a VT lookup answers with, and where they are from.
    Both halves are named in full -- the split is the thing being confirmed,
    and "62% U.S." alone leaves the reader to do the subtraction."""
    return (
        f"{format_percent(us_pct)}% U.S. stocks and "
        f"{format_percent(Decimal(100) - us_pct)}% international stocks "
        f"({describe_as_of(as_of)})."
    )


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

    # The fund is spelled out the first time it is named and abbreviated
    # after. Which line does the naming depends on which source answers --
    # --offline skips straight past the lookup to the manual prompt -- so it
    # is decided here rather than baked into one message.
    named = False

    def vt_possessive() -> str:
        nonlocal named
        if named:
            return f"{VT_TICKER}'s"
        named = True
        return f"{VT_FUND_NAME}'s ({VT_TICKER})"

    if not offline:
        prompter.say_wrapped(
            f"Looking up {vt_possessive()} current U.S. and international stock allocation..."
        )
        spoken = True
        try:
            result = fetch_vt_us_pct()
            with prompter.indented():
                prompter.say_wrapped(f"Found {_describe_us_ex_us(result.us_pct, result.as_of)}")
                if prompt_yes_no(prompter, "Use these values?", default=True):
                    return result
        except VTFetchError as exc:
            with prompter.indented():
                prompter.say_wrapped(f"Couldn't look up the current allocation ({exc}).")

    if cached_us_pct is not None:
        lead = "\n" if spoken else ""
        prompter.say_wrapped(
            f"{lead}Last saved: "
            f"{_describe_us_ex_us(cached_us_pct, cached_as_of or 'unknown date')}"
        )
        spoken = True
        with prompter.indented():
            if prompt_yes_no(prompter, "Use these values?", default=True):
                return VTAllocationResult(
                    us_pct=cached_us_pct, as_of=cached_as_of or "unknown date", source="cache"
                )

    suggested_default = cached_us_pct if cached_us_pct is not None else FALLBACK_VT_US_PCT
    lead = "\n" if spoken else ""
    prompter.say_wrapped(
        f"{lead}Please enter {vt_possessive()} U.S. stock allocation % manually "
        f"(see {VT_FUND_PAGE_URL})."
    )
    manual = prompt_percent(prompter, "U.S. stock", default=suggested_default)
    return VTAllocationResult(us_pct=manual, as_of="manually entered", source="manual")


# --------------------------------------------------------------------------
# Accounts
# --------------------------------------------------------------------------


def _describe_target_date_allocation(allocation: TargetDateAllocation) -> str:
    """The fund's own mix on one line. Shown before offering to change it, so
    the user is deciding against the numbers rather than from memory.

    A sentence, not a slash-separated fragment: it sits inside one, and the
    flow names a set of asset classes the same way wherever it does it."""
    return _and_list(
        [
            f"{format_percent(allocation.us_stock_pct)}% U.S. stocks",
            f"{format_percent(allocation.international_stock_pct)}% international stocks",
            f"{format_percent(allocation.bond_pct)}% bonds",
        ]
    )


def _prompt_target_date_allocation(prompter: Prompter) -> TargetDateAllocation:
    """The fund's own mix. Two questions, not three -- the bond sleeve is
    what the other two leave behind; see prompt_stock_bond_allocation for why
    the last of a set summing to 100 is stated rather than asked.

    Note this makes the three entered percentages sum to exactly 100, where a
    fact sheet rounding each sleeve to a tenth often does not.
    `TargetDateAllocation` still tolerates a sum that is off by
    PERCENT_SUM_TOLERANCE and `fraction_of` still normalizes, because a
    config file written by an older version -- or by hand -- can still hold
    one. The consequence to know about is that a fact sheet printing
    64.0 / 34.3 / 1.6 is confirmed back as 1.7% bonds, a tenth of a point off
    what it says. That difference survives no further than `fraction_of`,
    which would have spread the same tenth across the three sleeves anyway.
    """
    prompter.say("Enter this fund's underlying allocation (from its fact sheet):")
    with prompter.indented():
        while True:
            us_stock = prompt_percent(prompter, "U.S. stocks")
            # A question the answer above has already settled is not worth
            # asking: 100% U.S. stocks leaves nothing for either of the other
            # two sleeves, so both are derived and stated together.
            settled = us_stock == Decimal(100)
            if settled:
                international = Decimal(0)
            else:
                international = prompt_percent(prompter, "International stocks")
                if us_stock + international > Decimal(100):
                    prompter.say("Those add up to more than 100%. Let's try again.")
                    continue
            bond = Decimal(100) - us_stock - international
            derived = [(international, "international stocks")] if settled else []
            derived.append((bond, "bonds"))
            statement = "That leaves " + _and_list(
                [f"{format_percent(value)}% {label}" for value, label in derived]
            )
            if _confirm_remainder(prompter, f"{statement}.", len(derived)):
                return TargetDateAllocation(
                    us_stock_pct=us_stock, international_stock_pct=international, bond_pct=bond
                )


def _prompt_holding(
    prompter: Prompter, fund_type: FundType, label: str, *, existing: Holding | None = None
) -> Holding:
    """Ask for one fund: what it is at the current depth, everything about it
    one level deeper.

    The same helper serves a new account and a saved one -- `existing` only
    decides what the two questions offer as defaults, and whether a
    target-date fund's mix is asked for outright or shown for confirmation.
    One shape for both is what stops the two flows drifting apart.

    Depth comes from the prompter, never from spaces baked into the prompt
    text: this is called from two places at different depths, and a literal
    indent that lines up in one lands two levels off in the other.
    """
    name = prompt_str(
        prompter, label, default=existing.name if existing else None, reject_numeric=True
    )
    with prompter.indented():
        value = prompt_decimal(
            prompter,
            "Current value ($)",
            default=existing.value if existing else Decimal(0),
            min_value=Decimal(0),
        )
        allocation = existing.target_date_allocation if existing else None
        if fund_type == FundType.TARGET_DATE:
            if allocation is None:
                allocation = _prompt_target_date_allocation(prompter)
            else:
                prompter.say_wrapped(f"Currently {_describe_target_date_allocation(allocation)}")
                if prompt_yes_no(
                    prompter, "Update this fund's underlying allocation?", default=False
                ):
                    allocation = _prompt_target_date_allocation(prompter)
    return Holding(
        fund_type=fund_type, name=name, value=value, target_date_allocation=allocation
    )


def _prompt_cash(prompter: Prompter, *, default: Decimal = Decimal(0)) -> Holding | None:
    cash = prompt_decimal(
        prompter, "Cash available to invest ($)", default=default, min_value=Decimal(0)
    )
    return Holding(fund_type=FundType.CASH, name="", value=cash) if cash > 0 else None


def _slots_for(holdings: list[Holding]) -> list[tuple[FundType, str]]:
    """Which slots an account of this kind is asked about, in order. A
    target-date account has exactly one; anything else has all three."""
    if any(h.fund_type == FundType.TARGET_DATE for h in holdings):
        return [(FundType.TARGET_DATE, _TARGET_DATE_LABEL)]
    return _INDIVIDUAL_SLOT_PROMPTS


def _prompt_fund_holdings(prompter: Prompter) -> list[Holding]:
    """The funds an account holds -- one target-date fund, or all three
    individual funds. Never both; see Account's validation.

    All three, not a chosen subset: an account that holds individual funds is
    taken to be able to buy any of the three, so a fund the user owns none of
    is still worth a slot. That slot is capacity -- the solver can buy into
    it -- and gating each one behind "does this account hold...?" meant the
    truthful answer for a fund not yet bought silently removed the only place
    the plan could ever put that asset class. It also re-asked the question
    the choice below has just answered.
    """
    kind = prompt_choice(
        prompter,
        "What does this account hold?",
        _HOLDING_KIND_CHOICES,
        default=_INDIVIDUAL_FUNDS_CHOICE,
    )
    # Both branches open with a blank line, so the fund questions are set off
    # from the choice above them whichever way it was answered.
    if kind == _TARGET_DATE_CHOICE:
        prompter.say("")
        return [_prompt_holding(prompter, FundType.TARGET_DATE, _TARGET_DATE_LABEL)]

    prompter.say_wrapped("\n" + FUND_EXPLANATION)
    prompter.say("")
    return [
        _prompt_holding(prompter, fund_type, label)
        for fund_type, label in _INDIVIDUAL_SLOT_PROMPTS
    ]


def _prompt_holdings(prompter: Prompter) -> list[Holding]:
    holdings = _prompt_fund_holdings(prompter)
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
            "Account nickname (must be unique, e.g. 'Vanguard Roth IRA')",
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
            holdings = _prompt_holdings(prompter)
    return Account(
        account_type=account_type,
        name=name,
        tax_treatment=tax_treatment,
        holdings=holdings,
    )


def _prompt_update_existing_account(prompter: Prompter, existing: Account) -> Account:
    """Re-ask a saved account's holdings, its own answers pre-filled.

    Every question sits at the depth the same question sits at when the
    account is new -- one flow asking the same things twice should look the
    same both times, and it now does ask the same things.

    Both the ticker and the value are offered as editable defaults. A fund's
    name was fixed once saved, which was survivable while every declared
    holding was something the user owned; with a slot standing open for a
    fund they have not bought, the name is the part most likely to change --
    a plan swaps its bond fund, or the user picks a different one than the
    slot was opened with. Making it editable is also why no slot ever needs
    removing: a lineup change is a rename.
    """
    by_type = {h.fund_type: h for h in existing.holdings if h.fund_type != FundType.CASH}
    if by_type:
        new_holdings = [
            _prompt_holding(prompter, fund_type, label, existing=by_type.get(fund_type))
            for fund_type, label in _slots_for(existing.holdings)
        ]
    else:
        # Cash and nothing else: this account never committed to either kind,
        # so ask as though it were new.
        new_holdings = _prompt_fund_holdings(prompter)

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
    """Every saved account, offered back with its own answers pre-filled,
    then however many new ones are added.

    The saved accounts are listed vertically first: those names are the
    headings the questions below arrive in, and a list read down the page is
    what lets someone match one to the next. How to answer them is said once,
    above the list, because it is the same instruction for every account in
    it -- repeating "press Enter to keep the last value" at the head of each
    one said nothing the previous account had not already said.
    """
    accounts: list[Account] = []
    if existing_accounts:
        prompter.say("\n" + format_subheading("Saved Accounts"))
        noun = "account" if len(existing_accounts) == 1 else "accounts"
        # A vertical list, not a sentence: these are the headings the
        # questions below arrive in, and a list read down the page is what
        # lets someone match one to the next.
        prompter.say_wrapped(f"You have {len(existing_accounts)} saved {noun}:")
        with prompter.indented():
            for existing in existing_accounts:
                prompter.say(existing.name)
        prompter.say_wrapped(
            "\nFor each, press Enter to use its saved value, or type a new value."
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
    # you to do something, unlike "Saved Accounts" above, which names a list.
    heading = "Add More Accounts" if existing_accounts else "Add Accounts"
    prompter.say("\n" + format_subheading(heading))
    is_first_prompt = True
    listed_account_types = False
    while True:
        label = "Add an account?" if not accounts else "Add another account?"
        # Only the first question sits directly under the subheading; later
        # ones need a blank line to separate them from the account above.
        question = label if is_first_prompt else f"\n{label}"
        if not prompt_yes_no(prompter, question, default=not accounts):
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
