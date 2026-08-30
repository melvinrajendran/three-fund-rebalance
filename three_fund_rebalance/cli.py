"""Command-line entry point: orchestrates the interactive flow end-to-end.

    stock and bond target -> VT's U.S. allocation -> rebalancing band
    -> per-account holdings -> LP rebalance -> report -> optionally persist

Three of those are questions put to the user and are banners as numbered
steps; the report is what they produce, so it gets a banner of its own
without a step number.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path

from three_fund_rebalance import __version__
from three_fund_rebalance.allocation import compute_target_allocation
from three_fund_rebalance.config import DEFAULT_CONFIG_PATH
from three_fund_rebalance.formatting import (
    format_account_heading,
    format_percent,
    format_result_header,
    format_section_header,
    format_subheading,
)
from three_fund_rebalance.models import Account
from three_fund_rebalance.persistence import (
    PersistedConfig,
    PersistenceError,
    load_config,
    save_config,
)
from three_fund_rebalance.prompts import (
    ADD_ACCOUNTS_SUBHEADING,
    BAND_EXPLANATION,
    NOTHING_TO_UPDATE,
    REBALANCING_BANDS_SUBHEADING,
    SAVE_PORTFOLIO_SUBHEADING,
    STOCK_BOND_SUBHEADING,
    VT_SPLIT_SUBHEADING,
    Prompter,
    prompt_accounts,
    prompt_add_accounts,
    prompt_rebalance_band,
    prompt_relative_rebalance_band,
    prompt_revise_account,
    prompt_revision_choice,
    prompt_stock_bond_allocation,
    prompt_yes_no,
    resolve_vt_allocation,
)
from three_fund_rebalance.rebalance import RebalanceError, compute_trades
from three_fund_rebalance.report import DISCLAIMER, RebalanceInputs, format_report
from three_fund_rebalance.vt_allocation import VTAllocationResult

#: How many numbered steps the user is asked to walk through. The report
#: that follows them is not one of them.
_INPUT_STEPS = 3

#: What the report is called, on screen and in the file name it is written
#: to. One string, so "rebalancing summary" and "rebalancing-summary-...txt"
#: cannot come to mean different things.
_SUMMARY_TITLE = "Rebalancing summary"


def _decimal_arg(raw: str) -> Decimal:
    try:
        return Decimal(raw)
    except InvalidOperation as exc:
        raise argparse.ArgumentTypeError(f"{raw!r} is not a valid number") from exc


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="three-fund-rebalance",
        description=(
            "Calculate the trades needed to rebalance a three-fund portfolio "
            "across every account it is held in."
        ),
        # Someone who runs --help and stops there never sees a report, so the
        # report's own disclaimer goes here -- the same string, not a second
        # wording of it, so the two cannot drift apart. argparse reflows it,
        # so it has to read as prose rather than rely on a line break.
        epilog=DISCLAIMER,
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=DEFAULT_CONFIG_PATH,
        help="Portfolio file to read and write (default: %(default)s)",
    )
    parser.add_argument(
        "--fresh", action="store_true", help="Ignore the saved portfolio and start blank"
    )
    parser.add_argument(
        "--no-save", action="store_true", help="Don't offer to save this run's answers"
    )
    parser.add_argument(
        "--offline",
        action="store_true",
        help="Skip the live VT fetch; use the saved or a manually entered value instead",
    )
    parser.add_argument(
        "--vt-us-pct",
        type=_decimal_arg,
        default=None,
        metavar="PCT",
        help="Set VT's U.S. stock allocation %% directly, skipping the lookup and the prompt",
    )
    return parser.parse_args(argv)


@dataclass
class _Answers:
    """Everything the three steps collected, mutable so one answer can be
    re-asked in place without re-walking the flow around it."""

    stock_pct: Decimal
    bond_pct: Decimal
    vt: VTAllocationResult
    band_pct: Decimal
    relative_band_pct: Decimal | None
    accounts: list[Account]


def _ask_vt_split(prompter: Prompter, args, answers: _Answers) -> VTAllocationResult:
    """Step 1's second question, asked the same way the first time and every
    time after -- the saved split is offered back as the default."""
    if args.vt_us_pct is not None:
        result = VTAllocationResult(
            us_pct=args.vt_us_pct, as_of="manually specified via --vt-us-pct", source="manual"
        )
        prompter.say_wrapped(
            f"Using {format_percent(result.us_pct)}% U.S. stocks and "
            f"{format_percent(Decimal(100) - result.us_pct)}% international stocks, "
            f"as given by --vt-us-pct."
        )
        return result
    return resolve_vt_allocation(
        prompter,
        cached_us_pct=answers.vt.us_pct if answers.vt else None,
        cached_as_of=answers.vt.as_of if answers.vt else None,
        offline=args.offline,
    )


def _revise(prompter: Prompter, args, answers: _Answers) -> bool:
    """Re-ask one answer, in place. False if the user chose to change nothing.

    The menu offers each question by the `-` subheading it was asked under,
    and re-asking one reprints that subheading -- so a correction looks like
    the part of the flow it belongs to rather than like a separate editor.
    """
    choice = prompt_revision_choice(
        prompter, answers.accounts, include_vt_split=args.vt_us_pct is None
    )
    if choice == NOTHING_TO_UPDATE:
        return False
    if choice == STOCK_BOND_SUBHEADING:
        prompter.say("\n" + format_subheading(STOCK_BOND_SUBHEADING))
        answers.stock_pct, answers.bond_pct = prompt_stock_bond_allocation(
            prompter, default_stock=answers.stock_pct
        )
    elif choice == VT_SPLIT_SUBHEADING:
        prompter.say("\n" + format_subheading(VT_SPLIT_SUBHEADING))
        answers.vt = _ask_vt_split(prompter, args, answers)
    elif choice == REBALANCING_BANDS_SUBHEADING:
        prompter.say("\n" + format_subheading(REBALANCING_BANDS_SUBHEADING))
        prompter.say_wrapped(BAND_EXPLANATION)
        prompter.say("")
        answers.band_pct = prompt_rebalance_band(prompter, default=answers.band_pct)
        answers.relative_band_pct = prompt_relative_rebalance_band(
            prompter, default=answers.relative_band_pct
        )
    elif choice == ADD_ACCOUNTS_SUBHEADING:
        answers.accounts.extend(
            prompt_add_accounts(prompter, answers.accounts, had_saved=True)
        )
    else:
        # The remaining choices are the accounts, listed in their own order.
        index = next(
            i
            for i, account in enumerate(answers.accounts)
            if format_account_heading(account.name, account.account_type) == choice
        )
        revised = prompt_revise_account(prompter, answers.accounts[index])
        if revised is None:
            del answers.accounts[index]
        else:
            answers.accounts[index] = revised
    return True


def run(argv: list[str] | None = None, prompter: Prompter | None = None) -> int:
    args = parse_args(argv)
    prompter = prompter or Prompter()

    config = PersistedConfig()
    if not args.fresh:
        try:
            config = load_config(args.config)
        except PersistenceError as exc:
            prompter.say_wrapped(
                f"Warning: could not read the saved portfolio at {args.config} "
                f"({exc}). Starting from scratch."
            )

    prompter.say("\n" + format_section_header(1, _INPUT_STEPS, "Target asset allocation"))

    prompter.say("\n" + format_subheading(STOCK_BOND_SUBHEADING))
    stock_pct, bond_pct = prompt_stock_bond_allocation(
        prompter, default_stock=config.stock_pct
    )

    prompter.say("\n" + format_subheading(VT_SPLIT_SUBHEADING))
    if args.vt_us_pct is not None:
        vt_result = VTAllocationResult(
            us_pct=args.vt_us_pct, as_of="manually specified via --vt-us-pct", source="manual"
        )
        prompter.say_wrapped(
            f"Using {format_percent(vt_result.us_pct)}% U.S. stocks and "
            f"{format_percent(Decimal(100) - vt_result.us_pct)}% international stocks, "
            f"as given by --vt-us-pct."
        )
    else:
        vt_result = resolve_vt_allocation(
            prompter,
            cached_us_pct=config.vt_us_pct,
            cached_as_of=config.vt_as_of,
            offline=args.offline,
        )

    # The banner names the question, the subheading names the mechanism --
    # the same shape as step 1, and it lets the band be called a band here as
    # it is in the report, the README and the saved config.
    prompter.say("\n" + format_section_header(2, _INPUT_STEPS, "When to rebalance"))

    prompter.say("\n" + format_subheading(REBALANCING_BANDS_SUBHEADING))
    # The one place the flow explains before it asks; see BAND_EXPLANATION.
    prompter.say_wrapped(BAND_EXPLANATION)
    prompter.say("")
    band_pct = prompt_rebalance_band(prompter, default=config.rebalance_band_pct)
    relative_band_pct = prompt_relative_rebalance_band(
        prompter, default=config.rebalance_relative_band_pct
    )

    prompter.say("\n" + format_section_header(3, _INPUT_STEPS, "Account holdings"))
    accounts = prompt_accounts(prompter, config.accounts)
    if not accounts:
        prompter.say("\nNo accounts entered -- nothing to rebalance.")
        return 0

    answers = _Answers(
        stock_pct=stock_pct,
        bond_pct=bond_pct,
        vt=vt_result,
        band_pct=band_pct,
        relative_band_pct=relative_band_pct,
        accounts=accounts,
    )

    # Compute, show, and offer to correct one answer -- looping because the
    # report is where a typo is actually noticed. A wrong balance shows up as
    # an implausible order, a wrong ticker in Account Holdings, a wrong band
    # as "no trades needed"; none of them are visible at the prompt that
    # collected them, so a confirmation gate before the solve would ask
    # "is this right?" ahead of the only thing that answers it.
    inputs = None
    result = None
    while True:
        target = compute_target_allocation(
            answers.stock_pct, answers.bond_pct, answers.vt.us_pct
        )
        try:
            result = compute_trades(
                answers.accounts, target, answers.band_pct, answers.relative_band_pct
            )
        except RebalanceError as exc:
            # No plan this pass, and the previous pass's is not this one's --
            # the answers have moved on, so carrying it forward would report
            # and save a plan for a portfolio that is no longer described.
            inputs = result = None
            prompter.say_wrapped(f"\nCould not compute a rebalance: {exc}")
            # An unplannable portfolio is usually a mistyped answer, so the
            # same correction loop is the way out of it rather than a rerun.
            if not prompt_yes_no(prompter, "Update an answer and try again?", default=True):
                return 1
        else:
            inputs = RebalanceInputs(
                stock_pct=answers.stock_pct,
                bond_pct=answers.bond_pct,
                vt=answers.vt,
                target=target,
                band_pct=answers.band_pct,
                relative_band_pct=answers.relative_band_pct,
                accounts=answers.accounts,
                values_as_of=config.values_as_of,
            )
            prompter.say("\n" + format_result_header(_SUMMARY_TITLE))
            prompter.say("\n" + format_report(inputs, result))
            if not prompt_yes_no(
                prompter, "\nUpdate an answer and recompute?", default=False
            ):
                break

        # The menu carries the same way out, for a mind changed one question
        # later. Past a failed solve it is a decline rather than a pass --
        # there is no plan to go on to, and nothing has changed to make the
        # next attempt differ from the one that just failed.
        if not _revise(prompter, args, answers):
            if result is None:
                return 1
            break
        if not answers.accounts:
            prompter.say("\nNo accounts left -- nothing to rebalance.")
            return 0

    # A section of its own rather than a question tacked onto the end of the
    # report: it is a separate action, and the report now closes with a
    # disclaimer that should not read as part of the prompt.
    if not args.no_save:
        prompter.say("\n" + format_subheading(SAVE_PORTFOLIO_SUBHEADING))
        if prompt_yes_no(prompter, "Save this portfolio for next time?", default=True):
            updated = PersistedConfig(
                stock_pct=answers.stock_pct,
                bond_pct=answers.bond_pct,
                vt_us_pct=answers.vt.us_pct,
                vt_as_of=answers.vt.as_of,
                rebalance_band_pct=answers.band_pct,
                rebalance_relative_band_pct=answers.relative_band_pct,
                values_as_of=datetime.now(tz=timezone.utc).date().isoformat(),
                accounts=answers.accounts,
            )
            save_config(args.config, updated)
            prompter.say(f"Saved to {args.config}")
        else:
            # Without this the section is a heading, a question, and nothing.
            prompter.say("Not saved.")

    return 0


def main() -> None:
    sys.exit(run())


if __name__ == "__main__":
    main()
