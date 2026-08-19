"""Command-line entry point: orchestrates the interactive flow end-to-end.

    stock/bond target -> VT US/ex-US split -> per-account holdings
    -> LP rebalance -> report -> optionally persist for next time.
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path

from three_fund_rebalance.allocation import compute_target_allocation
from three_fund_rebalance.config import DEFAULT_CONFIG_PATH
from three_fund_rebalance.persistence import (
    PersistedConfig,
    PersistenceError,
    load_config,
    save_config,
)
from three_fund_rebalance.prompts import (
    Prompter,
    prompt_accounts,
    prompt_stock_bond_target,
    prompt_yes_no,
    resolve_vt_split,
)
from three_fund_rebalance.rebalance import RebalanceError, compute_trades
from three_fund_rebalance.report import format_report
from three_fund_rebalance.vt_allocation import VTAllocationResult


def _decimal_arg(raw: str) -> Decimal:
    try:
        return Decimal(raw)
    except InvalidOperation as exc:
        raise argparse.ArgumentTypeError(f"{raw!r} is not a valid number") from exc


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="three-fund-rebalance",
        description="Compute trades to rebalance a three-fund portfolio across accounts.",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=DEFAULT_CONFIG_PATH,
        help="Path to the portfolio config file (default: %(default)s)",
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
        help="Manually set VT's US stock allocation %% and skip fetching/prompting for it entirely",
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
            prompter.say(f"Warning: could not read config at {args.config} ({exc}). Starting fresh.")

    stock_pct, bond_pct = prompt_stock_bond_target(
        prompter, default_stock=config.stock_pct, default_bond=config.bond_pct
    )

    if args.vt_us_pct is not None:
        vt_result = VTAllocationResult(
            us_pct=args.vt_us_pct, as_of="manually specified via --vt-us-pct", source="manual"
        )
    else:
        vt_result = resolve_vt_split(
            prompter,
            cached_us_pct=config.vt_us_pct,
            cached_as_of=config.vt_as_of,
            offline=args.offline,
        )

    target = compute_target_allocation(stock_pct, bond_pct, vt_result.us_pct)

    accounts = prompt_accounts(prompter, config.accounts)
    if not accounts:
        prompter.say("\nNo accounts entered -- nothing to rebalance.")
        return 0

    try:
        result = compute_trades(accounts, target)
    except RebalanceError as exc:
        prompter.say(f"\nCould not compute a rebalance: {exc}")
        return 1

    prompter.say("\n" + format_report(accounts, target, result))

    if not args.no_save and prompt_yes_no(
        prompter, "\nSave this configuration for next time?", default=True
    ):
        updated = PersistedConfig(
            stock_pct=stock_pct,
            bond_pct=bond_pct,
            vt_us_pct=vt_result.us_pct,
            vt_as_of=vt_result.as_of,
            balances_as_of=datetime.now(tz=timezone.utc).date().isoformat(),
            accounts=accounts,
        )
        save_config(args.config, updated)
        prompter.say(f"Saved to {args.config}")

    return 0


def main() -> None:
    sys.exit(run())


if __name__ == "__main__":
    main()
