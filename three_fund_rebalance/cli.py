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
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path

from three_fund_rebalance import __version__
from three_fund_rebalance.allocation import compute_target_allocation
from three_fund_rebalance.config import DEFAULT_CONFIG_PATH
from three_fund_rebalance.formatting import (
    format_percent,
    format_result_header,
    format_section_header,
    format_subheading,
)
from three_fund_rebalance.persistence import (
    PersistedConfig,
    PersistenceError,
    load_config,
    save_config,
)
from three_fund_rebalance.prompts import (
    Prompter,
    prompt_accounts,
    prompt_rebalance_band,
    prompt_stock_bond_allocation,
    prompt_yes_no,
    resolve_vt_allocation,
)
from three_fund_rebalance.rebalance import RebalanceError, compute_trades
from three_fund_rebalance.report import RebalanceInputs, format_report
from three_fund_rebalance.vt_allocation import VTAllocationResult

#: How many numbered steps the user is asked to walk through. The report
#: that follows them is not one of them.
_INPUT_STEPS = 3


def _decimal_arg(raw: str) -> Decimal:
    try:
        return Decimal(raw)
    except InvalidOperation as exc:
        raise argparse.ArgumentTypeError(f"{raw!r} is not a valid number") from exc


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="three-fund-rebalance",
        description="Calculate the trades needed to rebalance a three-fund portfolio across your accounts.",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=(
            # argparse reflows this, so it has to read as prose rather than
            # relying on a line break to separate the two sentences.
            f"%(prog)s {__version__}. "
            "Not affiliated with, endorsed by, or sponsored by Vanguard, Fidelity, "
            "or any broker or fund company."
        ),
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=DEFAULT_CONFIG_PATH,
        help="Where your saved portfolio is read from and written to (default: %(default)s)",
    )
    parser.add_argument(
        "--fresh", action="store_true", help="Ignore any existing config file and start blank"
    )
    parser.add_argument(
        "--no-save", action="store_true", help="Don't persist changes to the config file at the end"
    )
    parser.add_argument(
        "--offline",
        action="store_true",
        help="Skip the live VT fetch; use the cached or manually entered value instead",
    )
    parser.add_argument(
        "--vt-us-pct",
        type=_decimal_arg,
        default=None,
        metavar="PCT",
        help="Manually set VT's U.S. stock allocation %% and skip looking it up or prompting for it",
    )
    return parser.parse_args(argv)


def run(argv: list[str] | None = None, prompter: Prompter | None = None) -> int:
    args = parse_args(argv)
    prompter = prompter or Prompter()

    config = PersistedConfig()
    if not args.fresh:
        try:
            config = load_config(args.config)
        except PersistenceError as exc:
            prompter.say_wrapped(
                f"Warning: could not read your saved portfolio at {args.config} "
                f"({exc}). Starting from scratch."
            )

    prompter.say("\n" + format_section_header(1, _INPUT_STEPS, "Target asset allocation"))

    prompter.say("\n" + format_subheading("Stock and bond allocation"))
    stock_pct, bond_pct = prompt_stock_bond_allocation(
        prompter, default_stock=config.stock_pct, default_bond=config.bond_pct
    )

    prompter.say("\n" + format_subheading("U.S. and international stock allocation"))
    if args.vt_us_pct is not None:
        vt_result = VTAllocationResult(
            us_pct=args.vt_us_pct, as_of="manually specified via --vt-us-pct", source="manual"
        )
        prompter.say(
            f"Using {format_percent(vt_result.us_pct)}% U.S. / "
            f"{format_percent(Decimal(100) - vt_result.us_pct)}% international, "
            f"as given by --vt-us-pct."
        )
    else:
        vt_result = resolve_vt_allocation(
            prompter,
            cached_us_pct=config.vt_us_pct,
            cached_as_of=config.vt_as_of,
            offline=args.offline,
        )

    target = compute_target_allocation(stock_pct, bond_pct, vt_result.us_pct)

    # The banner names the question, the subheading names the mechanism --
    # the same shape as step 1, and it lets the band be called a band here as
    # it is in the report, the README and the saved config.
    prompter.say("\n" + format_section_header(2, _INPUT_STEPS, "When to rebalance"))

    prompter.say("\n" + format_subheading("Rebalancing band"))
    band_pct = prompt_rebalance_band(prompter, default=config.rebalance_band_pct)

    prompter.say("\n" + format_section_header(3, _INPUT_STEPS, "Account holdings"))
    accounts = prompt_accounts(prompter, config.accounts)
    if not accounts:
        prompter.say("\nNo accounts entered -- nothing to rebalance.")
        return 0

    try:
        result = compute_trades(accounts, target, band_pct)
    except RebalanceError as exc:
        prompter.say_wrapped(f"\nCould not compute a rebalance: {exc}")
        return 1

    inputs = RebalanceInputs(
        stock_pct=stock_pct,
        bond_pct=bond_pct,
        vt=vt_result,
        target=target,
        band_pct=band_pct,
        accounts=accounts,
        values_as_of=config.values_as_of,
    )
    prompter.say("\n" + format_result_header("Rebalancing summary"))
    prompter.say("\n" + format_report(inputs, result))

    # A section of its own rather than a question tacked onto the end of the
    # report: it is a separate action, and the report now closes with a
    # disclaimer that should not read as part of the prompt.
    if not args.no_save:
        prompter.say("\n" + format_subheading("Save your portfolio"))
        if prompt_yes_no(prompter, "Save this portfolio for next time?", default=True):
            updated = PersistedConfig(
                stock_pct=stock_pct,
                bond_pct=bond_pct,
                vt_us_pct=vt_result.us_pct,
                vt_as_of=vt_result.as_of,
                rebalance_band_pct=band_pct,
                values_as_of=datetime.now(tz=timezone.utc).date().isoformat(),
                accounts=accounts,
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
