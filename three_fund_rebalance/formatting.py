"""Shared presentation primitives for the CLI's on-screen structure.

Hierarchy is carried by two devices and no more: a rule under a heading, and
indentation. `=` banners the three top-level steps and `-` underlines the
divisions within a step; below that, nesting is shown by position alone. An
account is therefore a plain label -- it is the only thing at its depth, and
indenting its contents one level further says everything a third rule style
would have said, without the ink.
"""

from __future__ import annotations

SECTION_RULE_WIDTH = 52

#: One level of nesting. Below the ruled headings, depth is the whole system.
INDENT_UNIT = "  "


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
    """Names one investment account. Unruled: callers set it off by indenting
    it under its subheading and indenting its contents one level deeper. Used
    for the same account in both the holdings questions and the trade listing,
    so a given account reads the same wherever it appears."""
    return f"{name} ({account_type})"
