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
    DEFAULT_REBALANCE_RELATIVE_BAND_PCT,
    FALLBACK_VT_US_PCT,
    MAX_ACCOUNT_NAME_LENGTH,
    VT_FUND_NAME,
    VT_FUND_PAGE_URL,
    VT_TICKER,
    infer_tax_treatment,
)
from three_fund_rebalance.formatting import (
    ASSET_CLASS_LABELS,
    INDENT_UNIT,
    describe_as_of,
    describe_fund_allocation,
    format_account_heading,
    format_and_list,
    format_percent,
    format_subheading,
    prose_width,
    wrap,
)
from three_fund_rebalance.models import (
    Account,
    FundAllocation,
    FundCatalog,
    FundProfile,
    FundType,
    Holding,
    TaxTreatment,
    fund_name_key,
)
from three_fund_rebalance.vt_allocation import VTAllocationResult, VTFetchError, fetch_vt_us_pct

# (fund type, what the question calls it) -- also the order the kinds are
# offered in, which runs the three asset classes in the order every table in
# the report lists them and puts the mix last, since it is the one answer
# that asks a follow-up question.
#
# Plural, because these stand on their own as answers to "what does this
# fund hold?" rather than sitting before the word "fund" -- the same split
# report._CATEGORY_LABELS keeps, and the opposite of ASSET_CLASS_LABELS,
# which is attributive ("a bond fund", the way it is a shoe store).
_FUND_KIND_CHOICES: list[tuple[FundType, str]] = [
    (FundType.US_STOCK, "U.S. stocks"),
    (FundType.INTERNATIONAL_STOCK, "International stocks"),
    (FundType.US_BOND, "Bonds"),
    (FundType.MULTI_ASSET, "A mix of asset classes"),
]
_FUND_KIND_LABELS = [label for _, label in _FUND_KIND_CHOICES]
_FUND_TYPE_BY_KIND = dict(zip(_FUND_KIND_LABELS, [t for t, _ in _FUND_KIND_CHOICES], strict=True))
_KIND_BY_FUND_TYPE = {fund_type: label for label, fund_type in _FUND_TYPE_BY_KIND.items()}

#: The `-` subheadings the flow asks its questions under, in one place
#: because the revision menu offers them back as its own choices -- someone
#: correcting a typo picks the heading they answered it under, and a menu
#: that paraphrased those headings would be a second name for each question.
#: Title Case, like every other `-` subheading; see formatting.py.
STOCK_BOND_SUBHEADING = "Stock and Bond Allocation"
VT_SPLIT_SUBHEADING = "U.S. and International Stock Allocation"
REBALANCING_BANDS_SUBHEADING = "Rebalancing Bands"
SAVED_ACCOUNTS_SUBHEADING = "Saved Accounts"
ADD_ACCOUNTS_SUBHEADING = "Add Accounts"
ADD_MORE_ACCOUNTS_SUBHEADING = "Add More Accounts"
UPDATE_ANSWER_SUBHEADING = "Update Answer"
SAVE_PORTFOLIO_SUBHEADING = "Save Portfolio"

#: The way out of the update menu without touching anything. Last, because
#: reaching this menu means having already said yes to updating something --
#: it is the change of mind, not the expected answer. "No Updates" answers
#: "What would you like to update?" in the words the question asked it; the
#: second half says what happens next, since every other entry visibly leads
#: somewhere. Title Case like the subheadings it sits among.
NOTHING_TO_UPDATE = "No Updates, Continue"

#: Said once, above an account's fund questions. The flow otherwise asks bare
#: questions and lets the report explain, but the loop below asks "add another
#: fund?" without ever saying which funds belong in the answer -- and the two
#: things a reader would get wrong are both settled here. A fund the account
#: *could* buy but does not hold yet still belongs, because a declared fund is
#: the only place the plan can ever put an asset class; and a fund left out is
#: not merely unreported, it is one the plan may not use at all.
#:
#: The failure mode is hesitation rather than confusion -- "including any held
#: at $0" is what answers it, and it has to sit in the same sentence as the
#: instruction or it reads as a footnote about a rare case.
FUND_EXPLANATION = (
    "Enter every fund this account can buy or sell -- only these can be traded. "
    "A $0 position is fine."
)

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

    `reject_numeric` guards the one question that takes a different kind of
    answer from the ones around it. A fund's name opens the block its value
    closes, and on a saved fund the name arrives pre-filled while the value
    is the only thing that changed quarter to quarter -- so typing the new
    value at the name prompt is the natural slip, and nothing else here
    catches it. The amount becomes the fund's name, is saved to the config
    file, and comes back in the plan as "Buy $29,500.00 of 178000". No fund
    name or ticker is a bare number, so refusing one costs nothing.

    The kind question now sits between the two, which makes the slip a little
    less likely and the check no less worth having: it is the only thing
    standing between a mistyped value and a wrong order that looks right.
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
#: the 5/25 rule, which both questions offer as their default.
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

    Offers the 5 of the 5/25 rule unless the user has a saved answer, which
    wins -- see prompt_relative_rebalance_band.
    """
    return prompt_percent(
        prompter,
        "Absolute band, in percentage points of the portfolio",
        default=DEFAULT_REBALANCE_BAND_PCT if default is None else default,
        unit="pts",
    )


def prompt_relative_rebalance_band(
    prompter: Prompter, *, default: Decimal | None = None
) -> Decimal:
    """The relative band: a share of the class's own target, so it scales
    with the target where the absolute band does not.

    Like the absolute half, this offers the user's saved answer when there is
    one and the 25 of the 5/25 rule when there is not, so a first run can
    accept the convention with Enter and anyone can type over it.
    """
    return prompt_percent(
        prompter,
        "Relative band, as a percentage of the asset class's target",
        default=DEFAULT_REBALANCE_RELATIVE_BAND_PCT if default is None else default,
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


def _prompt_fund_allocation(prompter: Prompter) -> FundAllocation:
    """The fund's own mix. Two questions, not three -- the bond sleeve is
    what the other two leave behind; see prompt_stock_bond_allocation for why
    the last of a set summing to 100 is stated rather than asked.

    Note this makes the three entered percentages sum to exactly 100, where a
    fact sheet rounding each sleeve to a tenth often does not.
    `FundAllocation` still tolerates a sum that is off by
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
            statement = "That leaves " + format_and_list(
                [f"{format_percent(value)}% {label}" for value, label in derived]
            )
            if _confirm_remainder(prompter, f"{statement}.", len(derived)):
                return FundAllocation(
                    us_stock_pct=us_stock, international_stock_pct=international, bond_pct=bond
                )


def _describe_fund_profile(profile: FundProfile) -> str:
    """A saved fund as one sentence: "VTI is a U.S. stock fund." or "VBIAX is
    a multi-asset fund that holds 60% U.S. stocks, 0% international stocks,
    and 40% bonds."

    Named by `ASSET_CLASS_LABELS`, the attributive labels the report already
    calls a fund by, so a fund is "a bond fund" here exactly as it is there.
    """
    label = ASSET_CLASS_LABELS[profile.fund_type]
    article = "an" if label[0] in "aeiou" else "a"
    sentence = f"{profile.name} is {article} {label} fund"
    if profile.fund_type == FundType.MULTI_ASSET:
        sentence += f" that holds {describe_fund_allocation(profile.allocation)}"
    return f"{sentence}."


def _prompt_fund_details(
    prompter: Prompter, name: str, *, defaults: FundProfile | None, list_kinds: bool
) -> FundProfile:
    """What a fund holds, and its mix if that is several -- everything about a
    fund that is the same in every account holding it.

    Every answer offers `defaults` back as an editable default, the *kind*
    included: a lineup change where a balanced fund replaces an index fund is
    then one keystroke rather than a removal and a re-entry. A fund whose kind
    is changed *to* multi-asset has no saved mix, so it is asked for outright
    -- which is the same branch a brand new fund takes.
    """
    kind = prompt_choice(
        prompter,
        "What does this fund hold?",
        _FUND_KIND_LABELS,
        default=_KIND_BY_FUND_TYPE[defaults.fund_type] if defaults else None,
        list_choices=list_kinds,
    )
    fund_type = _FUND_TYPE_BY_KIND[kind]

    # Carried over only when the kind did not change; a fund that has just
    # become multi-asset has no mix yet, and one that has just stopped being
    # multi-asset may not keep the one it had.
    allocation = (
        defaults.allocation if defaults is not None and defaults.fund_type == fund_type else None
    )
    if fund_type == FundType.MULTI_ASSET:
        if allocation is None:
            allocation = _prompt_fund_allocation(prompter)
        else:
            prompter.say_wrapped(f"Currently {describe_fund_allocation(allocation)}")
            if prompt_yes_no(prompter, "Update this fund's underlying allocation?", default=False):
                allocation = _prompt_fund_allocation(prompter)
    return FundProfile(name=name, fund_type=fund_type, allocation=allocation)


def _prompt_holding(
    prompter: Prompter,
    *,
    existing: Holding | None = None,
    list_kinds: bool = True,
    taken_names: set[str] | None = None,
    catalog: FundCatalog | None = None,
) -> Holding:
    """Ask for one fund: what it is called at the current depth, everything
    about it one level deeper.

    The same helper serves a new fund and a saved one -- `existing` only
    decides what the questions offer as defaults. One shape for both is what
    stops the two flows drifting apart.

    **A fund name maps to one set of details across every account**, kept in
    `catalog`. A name the catalog already knows -- typed into a second
    account, or re-added later in the run it was removed in -- has its details shown and
    confirmed rather than asked again; a saved fund keeping its own name
    takes its defaults from the catalog rather than from its own copy, so an
    edit made to the same fund earlier in the run is what it offers. Whatever
    is answered is recorded back, and a change to a known fund's details says
    that it reaches every account holding it: `prompt_accounts` and the
    revise loop re-read every holding from the catalog, so it does.

    `taken_names` are the other funds already in this account, normalized the
    way `Account` compares them. An account may not hold two funds under one
    name -- that name is what an order is placed against -- and with the fund
    list now free-form that is a thing a user can type rather than a thing the
    model made unreachable, so it is caught here as a re-ask instead of
    surfacing as a `ValueError` out of `Account`. Same shape as the duplicate
    account nickname a few questions up.

    Depth comes from the prompter, never from spaces baked into the prompt
    text: this is called from two places at different depths, and a literal
    indent that lines up in one lands two levels off in the other.
    """
    catalog = catalog if catalog is not None else FundCatalog()
    while True:
        name = prompt_str(
            prompter,
            "Fund name or ticker",
            default=existing.name if existing else None,
            reject_numeric=True,
        )
        if fund_name_key(name) not in (taken_names or set()):
            break
        prompter.say(
            f"'{name}' is already in this account -- please choose a different "
            "name or ticker."
        )
    known = catalog.get(name)
    keeping_own_name = existing is not None and fund_name_key(name) == fund_name_key(
        existing.name
    )
    with prompter.indented():
        if known is not None and not keeping_own_name:
            prompter.say_wrapped(
                f"Saved details: {_describe_fund_profile(known)}"
            )
            if prompt_yes_no(prompter, "Use these details?", default=True):
                profile = known
            else:
                # The saved spelling stays: "vbiax" typed into a second account
                # is a lookup, not a rename of the fund everywhere else.
                profile = _prompt_fund_details(
                    prompter, known.name, defaults=known, list_kinds=list_kinds
                )
        else:
            defaults = known or (FundProfile.of(existing) if existing else None)
            profile = _prompt_fund_details(
                prompter, name, defaults=defaults, list_kinds=list_kinds
            )
        if known is not None and not known.same_details(profile):
            prompter.say_wrapped(
                f"This changes {profile.name}'s details in every account that holds it."
            )
        catalog.set(profile)

        value = prompt_decimal(
            prompter,
            "Current value ($)",
            default=existing.value if existing else Decimal(0),
            min_value=Decimal(0),
        )
    return profile.holding(value, name=name)


def _prompt_cash(prompter: Prompter, *, default: Decimal = Decimal(0)) -> Holding | None:
    cash = prompt_decimal(
        prompter, "Cash available to invest ($)", default=default, min_value=Decimal(0)
    )
    return Holding(fund_type=FundType.CASH, name="", value=cash) if cash > 0 else None


def _prompt_fund_holdings(
    prompter: Prompter,
    existing: list[Holding] | None = None,
    catalog: FundCatalog | None = None,
) -> list[Holding]:
    """The funds an account holds, as a list the user builds rather than a
    fixed set of slots.

    An account may hold any combination -- funds dedicated to one asset class,
    multi-asset funds whose mix the user declares, several of either. What
    they all have in common is the thing FUND_EXPLANATION says: a fund that is
    entered is one the plan may trade, and a fund that is not is one it may
    not, whatever the account actually holds. So a fund worth $0 today still
    belongs in the answer -- that slot is capacity, and gating it behind
    "does this account hold...?" is what once meant the truthful answer for a
    fund not yet bought silently removed the only place an asset class could
    ever go.

    A saved account's funds are walked first, each behind the same
    "Keep this fund?" gate `prompt_revise_account` puts in front of an
    account: it is the only way to drop one from a list nothing else bounds,
    and one question shape for removing either is one fewer thing to learn.

    "Add another fund?" then defaults to yes. It costs a single "n" to finish
    and it leans the way the capacity argument above does -- an account is far
    more often one fund short of what it could hold than one too many.
    """
    # Said for an account being built from nothing, and not for one whose
    # funds are being re-confirmed: `prompt_accounts` has already said how a
    # saved answer is kept, and an instruction repeated under every account
    # every run says nothing the run before it did not.
    if not existing:
        prompter.say_wrapped("\n" + FUND_EXPLANATION)
        prompter.say("")

    holdings: list[Holding] = []
    # The four kinds are worth seeing once per account. Reprinting them under
    # every fund is most of the screen, so later funds ask in one line -- the
    # same treatment prompt_choice already gives the eleven account types.
    listed_kinds = False

    def taken() -> set[str]:
        """The names this account has collected so far, as `Account` compares
        them. A saved fund being re-asked is not in here yet, so pressing
        Enter through its own name never clashes with itself.
        """
        return {fund_name_key(h.name) for h in holdings}

    for saved in existing or []:
        if not prompt_yes_no(prompter, f"Keep {saved.name}?", default=True):
            prompter.say(f"Removed '{saved.name}'.")
            continue
        holdings.append(
            _prompt_holding(
                prompter,
                existing=saved,
                list_kinds=not listed_kinds,
                taken_names=taken(),
                catalog=catalog,
            )
        )
        listed_kinds = True
        prompter.say("")

    while True:
        # A new account has to name a fund before it can be asked whether it
        # wants another; a saved one that kept at least one is past that.
        if holdings and not prompt_yes_no(prompter, "Add another fund?", default=True):
            break
        holdings.append(
            _prompt_holding(
                prompter, list_kinds=not listed_kinds, taken_names=taken(), catalog=catalog
            )
        )
        listed_kinds = True
        prompter.say("")

    return holdings


def _prompt_holdings(
    prompter: Prompter, existing: Account | None = None, catalog: FundCatalog | None = None
) -> list[Holding]:
    holdings = _prompt_fund_holdings(
        prompter, existing.funds() if existing else None, catalog
    )
    cash_holding = _prompt_cash(
        prompter, default=existing.available_cash() if existing else Decimal(0)
    )
    if cash_holding:
        holdings.append(cash_holding)
    return holdings


def _prompt_new_account(
    prompter: Prompter,
    existing_names: set[str],
    *,
    list_account_types: bool = True,
    catalog: FundCatalog | None = None,
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
            holdings = _prompt_holdings(prompter, catalog=catalog)
    return Account(
        account_type=account_type,
        name=name,
        tax_treatment=tax_treatment,
        holdings=holdings,
    )


def _prompt_update_existing_account(
    prompter: Prompter, existing: Account, catalog: FundCatalog | None = None
) -> Account:
    """Re-ask a saved account's holdings, its own answers pre-filled.

    Every question sits at the depth the same question sits at when the
    account is new, and is asked by the same function -- one flow asking the
    same things twice should look the same both times.

    Name, kind and value are all offered as editable defaults. The name
    matters most: a slot standing open for a fund not yet bought is exactly
    the one whose ticker changes, when a plan swaps its bond fund or the user
    picks a different one than the slot was opened with. Removal is the
    "Keep this fund?" gate in `_prompt_fund_holdings` rather than a rename,
    since a fund list the user builds can shrink as well as grow.
    """
    return Account(
        account_type=existing.account_type,
        name=existing.name,
        tax_treatment=existing.tax_treatment,
        holdings=_prompt_holdings(prompter, existing, catalog),
    )


def prompt_accounts(
    prompter: Prompter,
    existing_accounts: list[Account],
    catalog: FundCatalog | None = None,
) -> list[Account]:
    """Every saved account, offered back with its own answers pre-filled,
    then however many new ones are added.

    `catalog` is every fund known so far, and is added to as funds are
    entered; without one, it starts from the saved accounts' own funds. What
    comes back is re-read from it, so a fund whose details were changed in a
    later account is changed in an earlier one too.

    The saved accounts are listed vertically first: those names are the
    headings the questions below arrive in, and a list read down the page is
    what lets someone match one to the next. How to answer them is said once,
    above the list, because it is the same instruction for every account in
    it -- repeating "press Enter to keep the last value" at the head of each
    one said nothing the previous account had not already said.
    """
    if catalog is None:
        catalog = FundCatalog(accounts=existing_accounts)
    accounts: list[Account] = []
    if existing_accounts:
        prompter.say("\n" + format_subheading(SAVED_ACCOUNTS_SUBHEADING))
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
            kept = prompt_revise_account(prompter, existing, catalog)
            if kept is not None:
                accounts.append(kept)

    accounts.extend(
        prompt_add_accounts(
            prompter, accounts, had_saved=bool(existing_accounts), catalog=catalog
        )
    )
    return catalog.resolve(accounts)


def prompt_add_accounts(
    prompter: Prompter,
    accounts: list[Account],
    *,
    had_saved: bool,
    catalog: FundCatalog | None = None,
) -> list[Account]:
    """The "add accounts" loop, on its own so step 3 and the revision menu
    share one implementation rather than two that drift.

    `accounts` is what has been collected so far -- read for the names a new
    one may not reuse, and for whether the first question defaults to yes.
    It is never appended to here; the new accounts come back as a list.
    """
    # Both states of this heading are imperative: it is a section that asks
    # you to do something, unlike "Saved Accounts" above, which names a list.
    if catalog is None:
        catalog = FundCatalog(accounts=accounts)
    heading = ADD_MORE_ACCOUNTS_SUBHEADING if had_saved else ADD_ACCOUNTS_SUBHEADING
    prompter.say("\n" + format_subheading(heading))
    added: list[Account] = []
    is_first_prompt = True
    listed_account_types = False
    while True:
        label = "Add an account?" if not accounts and not added else "Add another account?"
        # Only the first question sits directly under the subheading; later
        # ones need a blank line to separate them from the account above.
        question = label if is_first_prompt else f"\n{label}"
        if not prompt_yes_no(prompter, question, default=not accounts and not added):
            break
        # The eleven account types are worth seeing once. Reprinting them for
        # every account is most of the screen, so later ones ask in one line.
        added.append(
            _prompt_new_account(
                prompter,
                existing_names={a.name for a in (*accounts, *added)},
                list_account_types=not listed_account_types,
                catalog=catalog,
            )
        )
        listed_account_types = True
        is_first_prompt = False

    return added


def prompt_revise_account(
    prompter: Prompter, existing: Account, catalog: FundCatalog | None = None
) -> Account | None:
    """One saved account re-asked, exactly as step 3 asks it -- the same
    heading, the same "Keep this account?" gate, the same pre-filled answers.
    None means the user dropped it."""
    if catalog is None:
        catalog = FundCatalog(accounts=[existing])
    prompter.say("")
    with prompter.indented():
        prompter.say(format_account_heading(existing.name, existing.account_type))
        with prompter.indented():
            if not prompt_yes_no(prompter, "Keep this account?", default=True):
                prompter.say(f"Removed '{existing.name}'.")
                return None
            return _prompt_update_existing_account(prompter, existing, catalog)


def prompt_revision_choice(
    prompter: Prompter, accounts: list[Account], *, include_vt_split: bool
) -> str:
    """Which answer to go back and change, after the report has shown what
    the answers produced.

    Every choice is the exact `-` subheading the question was asked under, so
    picking one reads as returning to that part of the flow rather than as
    opening some new editor. Accounts are named the way step 3 names them,
    which is also the way the report's Account Holdings names them.

    `include_vt_split` is False when --vt-us-pct fixed the split on the
    command line -- re-asking a question the invocation has already answered
    would only offer to contradict it.
    """
    choices = [STOCK_BOND_SUBHEADING]
    if include_vt_split:
        choices.append(VT_SPLIT_SUBHEADING)
    choices.append(REBALANCING_BANDS_SUBHEADING)
    choices += [format_account_heading(a.name, a.account_type) for a in accounts]
    choices.append(ADD_ACCOUNTS_SUBHEADING)
    choices.append(NOTHING_TO_UPDATE)
    return prompt_choice(prompter, "\nWhat would you like to update?", choices)
