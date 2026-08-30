"""Shared presentation primitives for the CLI's on-screen structure.

Hierarchy is carried by two devices and no more: a rule under a heading, and
indentation. `=` banners the three top-level steps and the report they
produce, and `-` underlines the divisions within either; below that, nesting
is shown by position alone. An
account is therefore a plain label -- it is the only thing at its depth, and
indenting its contents one level further says everything a third rule style
would have said, without the ink.
"""

from __future__ import annotations

import re
import shutil
import textwrap
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from datetime import datetime, timedelta
from decimal import Decimal

from three_fund_rebalance.models import FundType, TaxTreatment

#: Widest a paragraph is allowed to get, however wide the terminal is. Long
#: prose lines are harder to read, not easier: the comfortable measure runs
#: to about 75 characters, and monospace sits at the low end of that because
#: every character is full width and the eye travels farther to find the next
#: line. Tables keep widening past this; paragraphs stop.
#:
#: 80 rather than 90 because they render identically -- the wash-sale warning
#: is seven lines either way, the disclaimer two -- so the wider setting buys
#: nothing and costs a longer return sweep. Only past 100 do paragraphs get
#: shorter, and that is the range where they get harder to read.
PROSE_MAX_WIDTH = 80


#: What a summary written to a file is laid out for. A file is read
#: anywhere -- a different terminal, an editor, a mail client -- so it cannot
#: be sized to the window that happened to produce it, or the same portfolio
#: would land at 78 columns from one machine and 198 from another. 80 is what
#: the README's example and the whole test suite already assume, so the file
#: matches the documented output rather than being a third width.
SUMMARY_FILE_WIDTH = 80

_forced_width: int | None = None


@contextmanager
def fixed_width(columns: int) -> Iterator[None]:
    """Lay out everything inside the block at `columns`, whatever the
    terminal is.

    Width is read globally, by `prose_width` and `table_width`, on the way
    down through every renderer -- so pinning it is a property of the render
    rather than an argument threaded through a dozen signatures. Restores the
    previous setting on the way out, including when nested.
    """
    global _forced_width
    previous = _forced_width
    _forced_width = columns
    try:
        yield
    finally:
        _forced_width = previous


def terminal_width() -> int:
    """The terminal's column count, or 80 when there is no terminal to ask.
    Reads $COLUMNS first, which is what makes width behaviour testable --
    unless `fixed_width` has pinned it, which is what writing to a file
    does."""
    if _forced_width is not None:
        return _forced_width
    return shutil.get_terminal_size(fallback=(80, 24)).columns


def prose_width() -> int:
    """Width for anything that wraps: paragraphs, notes, warnings, banners.
    Follows the terminal up to PROSE_MAX_WIDTH and no further."""
    return min(terminal_width() - 2, PROSE_MAX_WIDTH)


def table_width() -> int:
    """Width a table may occupy. Tables are sized to their own contents
    rather than padded out to this, so it is a budget rather than a target --
    what it buys is that a seven-figure portfolio, whose dollar columns are
    four characters wider than a five-figure one, is no longer squeezed
    against a fixed 78."""
    return terminal_width() - 2

#: What each fund type is called in anything the user reads. Kept here rather
#: than on FundType because the enum's values are a storage detail -- they go
#: into config.json verbatim -- while these are prose, and are shared by the
#: prompts, the report, and the solver's error messages.
ASSET_CLASS_LABELS: dict[FundType, str] = {
    FundType.US_STOCK: "U.S. stock",
    FundType.INTERNATIONAL_STOCK: "international stock",
    FundType.US_BOND: "bond",
    FundType.TARGET_DATE: "target-date",
    FundType.CASH: "cash",
}

#: What each tax treatment is called on screen. Same reasoning as
#: ASSET_CLASS_LABELS: the enum's values are what goes into config.json,
#: these are the prose the prompts and the report share.
TAX_TREATMENT_LABELS: dict[TaxTreatment, str] = {
    TaxTreatment.TAXABLE: "taxable",
    TaxTreatment.TAX_DEFERRED: "tax-deferred",
    TaxTreatment.TAX_FREE: "tax-free",
}

#: One level of nesting. Below the ruled headings, depth is the whole system.
INDENT_UNIT = "  "


def format_section_header(step: int, total: int, title: str) -> str:
    """Render the banner that separates the CLI's three major phases. Every
    banner is ruled to the same width regardless of title length, so the
    sections line up as the run scrolls past."""
    label = f"STEP {step} OF {total}: {title.upper()}"
    rule = "=" * max(len(label), prose_width())
    return f"{rule}\n{label}\n{rule}"


def wrap(
    text: str,
    *,
    width: int | None = None,
    indent: str = "",
    hanging_indent: str | None = None,
) -> str:
    """Reflow a paragraph to `width`, indenting every line. Written as one
    long string at the call site and broken here, so editing the wording
    never means re-breaking the lines by hand.

    Hyphens and long words are left intact. textwrap will otherwise split
    "tax-advantaged" and "cost-basis" across lines, which in a document about
    tax treatment reads like a different term, and would happily break a
    ticker symbol in half.

    `hanging_indent` sets the continuation lines apart from the first, for a
    line that is prose but sits inside an indented block -- a trade naming a
    fund by its full name rather than its ticker, say.
    """
    return textwrap.fill(
        text,
        width=prose_width() if width is None else width,
        initial_indent=indent,
        subsequent_indent=indent if hanging_indent is None else hanging_indent,
        break_on_hyphens=False,
        break_long_words=False,
    )


def format_percent(value: Decimal) -> str:
    """A percentage written the way a person would type it: trailing zeros
    trimmed, never in exponent form. 80 stays "80", 62.0 becomes "62", 61.9
    stays "61.9".

    This is for prompts and for echoing a value back, where a fixed number of
    decimal places is noise and an inconsistent one -- [80] next to [62.0] --
    looks like a bug. A percentage the program computed rather than one the
    user typed goes through `percent_places` first, which rounds it; see
    `format_percent_at`.
    """
    return f"{value.normalize():f}"


#: The most decimal places any percentage is ever shown to. A tenth of a
#: point of a portfolio is already below what anyone can trade to, and the
#: figures behind these are non-terminating divisions.
PERCENT_MAX_PLACES = 1


def percent_places(values: Iterable[Decimal]) -> int:
    """How many decimal places to write a set of percentages at: the fewest
    that still write every one of them exactly, once each is rounded to
    PERCENT_MAX_PLACES.

    Prose passes one value at a time, so each figure is written as short as
    it can be -- "20%" rather than "20.0%". A table passes a whole column,
    or every value sharing one unit, so the figures line up on the decimal
    point instead: a column holding 62.5 writes its 38 as "38.0".
    """
    places = 0
    for value in values:
        rounded = round_percent(value)
        exponent = rounded.normalize().as_tuple().exponent
        places = max(places, min(PERCENT_MAX_PLACES, -int(exponent)))
    return places


def round_percent(value: Decimal) -> Decimal:
    """A percentage at the precision it is displayed to. Rounding first is
    what lets `percent_places` see 19.999999 as the "20" it will print.

    Half-even, which is the decimal context's own default and therefore what
    `f"{value:.1f}"` has always done here -- a band edge of 6.25% has printed
    as 6.2% since before any of this, and a rounding rule is not the kind of
    thing to change as a side effect of a formatting change.
    """
    return value.quantize(Decimal(1).scaleb(-PERCENT_MAX_PLACES))


def format_percent_at(value: Decimal, places: int, *, signed: bool = False) -> str:
    """One percentage at a precision `percent_places` chose. `signed` keeps
    the leading "+" on a drift, where the direction is the point."""
    return f"{round_percent(value):{'+' if signed else ''}.{places}f}"


def format_percent_prose(value: Decimal) -> str:
    """One percentage in running prose, written as short as it goes: "20%"
    rather than "20.0%", but "62.5%" in full. Prose has no column to line up
    with, so each figure carries only the precision it needs."""
    return format_percent_at(value, percent_places([value]))


def format_percents(values: list[Decimal], *, signed: bool = False) -> list[str]:
    """A set of percentages sharing one unit in one table, all written at the
    same precision so their decimal points line up."""
    places = percent_places(values)
    return [format_percent_at(value, places, signed=signed) for value in values]


#: How every date is written out. The fact sheet's own spelling, which is
#: also the one a reader would say out loud.
_DATE_FORMAT = "%B %-d, %Y"


def _parse_date(raw: str) -> datetime | None:
    """The same date reaches the user in three spellings -- a JSON
    timestamp, a config file's ISO date, and the fact sheet's own long form,
    which round-trips through here unchanged -- and several of these fields
    carry a note ("manually entered") rather than a date at all. None says it
    was a note."""
    for parse in (datetime.fromisoformat, lambda text: datetime.strptime(text, "%B %d, %Y")):
        try:
            return parse(raw)
        except (ValueError, TypeError):
            continue
    return None


def format_date(raw: str) -> str:
    """A date the way it is written out in full -- "July 31, 2026". Dates are
    read in one place, so they are spelled one way wherever they came from.
    Anything that is not a date is passed through untouched."""
    parsed = _parse_date(raw)
    return parsed.strftime(_DATE_FORMAT) if parsed else (raw or "unknown date")


#: The instant a run produced its plan, rendered two ways -- as the sentence
#: at the head of the report, and as the middle of the summary file's name.
#: They are one decision deliberately spelled twice rather than two decisions
#: that happen to agree today: same clock, same precision, same zone label,
#: both read off the one `datetime` the caller passes in.
#:
#: **Local time, not UTC.** Nobody reads their own afternoon back as
#: "9:03 PM UTC" -- a stamp is there to answer "was this before or after I
#: moved that money", and a reader who has to do arithmetic first is being
#: handed a worse answer than no stamp. Minutes because a plan is re-run
#: within the day constantly, especially now that the report can be corrected
#: and recomputed in place; two runs inside one minute is what the file
#: name's collision suffix is for rather than something to solve with more
#: digits.
_GENERATED_TIME_FORMAT = "%B %-d, %Y at %-I:%M %p"
_GENERATED_FILENAME_TIME_FORMAT = "%Y-%m-%d-%H%M"

#: A zone abbreviation worth printing: two to five ASCII letters, which is
#: every real one ("EDT", "IST", "BST", "UTC", "AEDT"). Everything else falls
#: to the numeric offset, and the shapes it screens out are the point --
#: `tzname()` answers "+0545" for a zone with no abbreviation (Kathmandu,
#: Eucla), and on Windows it answers a full phrase like "Eastern Daylight
#: Time", *localized*, so a non-English machine would otherwise put non-ASCII
#: into a file name. One test, three problems, and a defined result for each
#: rather than a surprise on somebody else's laptop.
_ZONE_ABBREVIATION = re.compile(r"^[A-Za-z]{2,5}$")


def _zone_labels(moment: datetime) -> tuple[str, str]:
    """How this datetime's zone is named in prose and in a file name.

    The pair is returned together because they have to agree, and the only
    way to be sure of that is for one function to decide both.
    """
    name = moment.tzname() or ""
    if _ZONE_ABBREVIATION.match(name):
        return name, name.lower()
    offset = moment.utcoffset() or timedelta(0)
    minutes = int(offset.total_seconds()) // 60
    sign = "-" if minutes < 0 else "+"
    hours, minutes = divmod(abs(minutes), 60)
    # "+" is legal on every filesystem this runs on and needs no quoting in a
    # shell, so the file name can carry the offset as it reads.
    return f"UTC{sign}{hours:02d}:{minutes:02d}", f"utc{sign}{hours:02d}{minutes:02d}"


def format_generated_at(moment: datetime) -> str:
    """When the plan was made, written out the way every other date here is
    -- "August 29, 2026 at 9:03 PM EDT", or "... 9:03 PM UTC+05:45" where the
    zone has no abbreviation.

    The abbreviation is not only shorter than an offset: it is what tells the
    two 1:30 AMs of a fall-back apart, which a bare local time cannot.
    """
    return f"{moment.strftime(_GENERATED_TIME_FORMAT)} {_zone_labels(moment)[0]}"


def format_generated_at_for_filename(moment: datetime) -> str:
    """The same instant as a file name can carry it --
    "2026-08-29-2103-edt".

    Not the sentence above with the spaces taken out. A name has to sort,
    survive a shell without quoting, and be legal on Windows, which rules out
    the comma, the spaces and the colon at once. What it keeps is the clock,
    the precision and the zone, which is the part that has to agree.

    Local time costs the chronological sort across a fall-back hour and
    across a change of zone. Both are real and neither can lose a file:
    `cli._write_summary` opens a generated name exclusively and falls to a
    numbered sibling, so a repeated stamp is a suffix rather than an
    overwrite.
    """
    return f"{moment.strftime(_GENERATED_FILENAME_TIME_FORMAT)}-{_zone_labels(moment)[1]}"


def describe_as_of(raw: str) -> str:
    """The parenthetical a figure carries its provenance in. A real date gets
    "as of"; a note does not -- "as of manually entered" is not a sentence."""
    return f"as of {format_date(raw)}" if _parse_date(raw) else format_date(raw)


def format_result_header(title: str) -> str:
    """Banner the CLI's output. Ruled to the same width as the step banners,
    because it sits at the same level as them -- but it carries no "STEP x OF
    y", since it is what the steps produced rather than another question."""
    label = title.upper()
    rule = "=" * max(len(label), prose_width())
    return f"{rule}\n{label}\n{rule}"


def format_subheading(text: str) -> str:
    """A lighter underlined heading, for divisions *within* a step. Ruled to
    the width of its own text, so it never competes with a section banner."""
    return f"{text}\n{'-' * len(text)}"


def format_account_heading(name: str, account_type: str) -> str:
    """Names one investment account. Unruled: callers set it off by indenting
    it under its subheading and indenting its contents one level deeper. Used
    for the same account in both the holdings questions and the trade listing,
    so a given account reads the same wherever it appears."""
    return f"{name} ({account_type})"
