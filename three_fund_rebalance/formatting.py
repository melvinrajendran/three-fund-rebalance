"""Shared presentation primitives for the CLI's on-screen structure.

Three heading levels, used consistently across the run, heaviest first: `=`
banners the three top-level steps, `-` underlines divisions within a step,
and a bracketed label marks a single account. Accounts get their own level
because they are what the reader scans for -- both when entering holdings and
when reading back the trades to place -- but a label rather than a rule, so a
column of them stays quiet next to the ruled headings above.
"""

from __future__ import annotations

SECTION_RULE_WIDTH = 52


def format_section_header(step: int, total: int, title: str) -> str:
    """Render the banner that separates the CLI's three major phases. Every
    banner is ruled to the same width regardless of title length, so the
    sections line up as the run scrolls past."""
    label = f"STEP {step} OF {total}: {title.upper()}"
    rule = "=" * max(len(label), SECTION_RULE_WIDTH)
    return f"{rule}\n{label}\n{rule}"


def format_subheading(text: str) -> str:
    """A lighter underlined heading, for divisions *within* a step. Ruled to
    the width of its own text, so it never competes with a section banner."""
    return f"{text}\n{'-' * len(text)}"


def format_account_heading(name: str, account_type: str) -> str:
    """Third-level heading: one investment account. Used for the same account
    in both the holdings questions and the trade listing, so a given account
    looks the same wherever it appears."""
    return f"[ {name} -- {account_type} ]"
