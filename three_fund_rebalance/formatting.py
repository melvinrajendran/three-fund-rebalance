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

import shutil
import textwrap
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


def terminal_width() -> int:
    """The terminal's column count, or 80 when there is no terminal to ask.
    Reads $COLUMNS first, which is what makes width behaviour testable."""
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
    looks like a bug. Report output does the opposite and fixes every
    percentage at one decimal place; see report.py.
    """
    return f"{value.normalize():f}"


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
