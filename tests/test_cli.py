from decimal import Decimal

import pytest

from three_fund_rebalance import __version__
from three_fund_rebalance.cli import parse_args, run
from three_fund_rebalance.config import VT_FUND_PAGE_URL
from three_fund_rebalance.formatting import prose_width
from three_fund_rebalance.models import FundType
from three_fund_rebalance.persistence import load_config
from three_fund_rebalance.prompts import Prompter
from three_fund_rebalance.report import DISCLAIMER


class ScriptedPrompter(Prompter):
    def __init__(self, responses: list[str]):
        self._responses = list(responses)
        self.output: list[str] = []
        super().__init__(input_func=self._next, print_func=self.output.append)

    def _next(self, _text: str = "") -> str:
        if not self._responses:
            raise AssertionError("Ran out of scripted responses")
        return self._responses.pop(0)

    @property
    def full_output(self) -> str:
        return "\n".join(self.output)

    def all_consumed(self) -> bool:
        return not self._responses


def new_account_responses(
    account_type_index: str,
    nickname: str,
    us_stock_value: str,
    intl_value: str,
    bond_value: str,
    cash: str = "0",
) -> list[str]:
    return [
        account_type_index,
        nickname,
        "1",  # three individual funds rather than a target-date fund
        "VTI", us_stock_value,
        "VXUS", intl_value,
        "BND", bond_value,
        cash,
    ]


def target_date_account_responses(
    account_type_index: str, nickname: str, value: str, cash: str = "0"
) -> list[str]:
    """The other kind of account: one target-date fund and nothing else."""
    return [
        account_type_index,
        nickname,
        "2",  # holds a single target-date fund
        "Target 2050", value,
        "60", "20", "y",  # its underlying allocation; 20% bonds derived
        cash,
    ]


class TestArgParsing:
    def test_invalid_vt_us_pct_exits_with_usage_error(self, capsys):
        with pytest.raises(SystemExit) as exc_info:
            parse_args(["--vt-us-pct", "not-a-number"])
        assert exc_info.value.code == 2
        assert "not a valid number" in capsys.readouterr().err

    def test_help_carries_the_report_s_own_disclaimer(self, capsys):
        """Someone who runs --help and stops there never sees a report. It is
        the same string, not a second wording of it, so the two cannot drift
        apart."""
        with pytest.raises(SystemExit):
            parse_args(["--help"])
        out = " ".join(capsys.readouterr().out.split())
        assert " ".join(DISCLAIMER.split()) in out

    def test_version_flag_prints_version_and_exits_cleanly(self, capsys):
        with pytest.raises(SystemExit) as exc_info:
            parse_args(["--version"])
        assert exc_info.value.code == 0
        assert __version__ in capsys.readouterr().out


class TestEndToEndRun:
    def test_run_without_vt_flag_uses_resolve_vt_allocation_offline_path(self, tmp_path):
        # Without --vt-us-pct, run() should go through resolve_vt_allocation(); using
        # --offline with no cache exercises the manual-entry fallback without
        # touching the network.
        config_path = tmp_path / "config.json"
        responses = [
            "80", "y",  # stock target, then the derived 20% bonds confirmed
            "75",  # manual VT US % entry (offline, no cache)
            "0",  # rebalancing band -- exact target, as before it existed
            "0",  # ...and its relative half, which zero already settles
            "y",
            *new_account_responses("1", "Roth", "10000", "0", "0"),
            "n",
            "n",  # don't save
        ]
        prompter = ScriptedPrompter(responses)
        exit_code = run(["--config", str(config_path), "--offline"], prompter=prompter)
        assert exit_code == 0
        assert "Sell $4,000.00 of VTI" in prompter.full_output


    def test_full_run_computes_trades_and_saves_config(self, tmp_path):
        config_path = tmp_path / "config.json"
        responses = [
            "80", "y",  # stock target, then the derived 20% bonds confirmed
            "0",  # rebalancing band -- exact target, as before it existed
            "0",  # ...and its relative half, which zero already settles
            "y",  # Add an account?
            *new_account_responses("1", "Roth", "10000", "0", "0"),
            "n",  # Add another account?
            "y",  # Save this configuration?
        ]
        prompter = ScriptedPrompter(responses)
        exit_code = run(
            ["--config", str(config_path), "--vt-us-pct", "75"], prompter=prompter
        )

        assert exit_code == 0
        # U.S. 80*75%=60, international 20, bond 20 on a $10,000 account starting all-U.S.
        assert "Sell $4,000.00 of VTI" in prompter.full_output
        assert "Buy $2,000.00 of VXUS" in prompter.full_output
        assert "Buy $2,000.00 of BND" in prompter.full_output

        saved = load_config(config_path)
        assert saved.stock_pct == Decimal(80)
        assert saved.bond_pct == Decimal(20)
        assert saved.vt_us_pct == Decimal(75)
        assert len(saved.accounts) == 1
        # Persisted values reflect what the user *entered* (current holdings),
        # not the hypothetical post-trade recommendation.
        assert saved.accounts[0].get_holding(FundType.US_STOCK).value == Decimal(10_000)

    def test_both_halves_of_the_band_are_asked_for_and_saved(self, tmp_path):
        """Step 2 asks two questions now, and the answers come back as
        editable defaults on the next run like every other answer."""
        config_path = tmp_path / "config.json"
        responses = [
            "80", "y",
            "5",   # rebalancing band
            "25",  # ...as a share of each class's own target
            "y",
            *new_account_responses("1", "Roth", "10000", "0", "0"),
            "n",
            "y",
        ]
        prompter = ScriptedPrompter(responses)
        assert run(["--config", str(config_path), "--vt-us-pct", "75"], prompter=prompter) == 0

        saved = load_config(config_path)
        assert saved.rebalance_band_pct == Decimal(5)
        assert saved.rebalance_relative_band_pct == Decimal(25)
        # A 20% bond target and a 25% relative rule meet at 5 points, so the
        # bond band is 15.0% to 25.0%; U.S. stock at 60% gets the same 5.
        assert "Bonds                 15.0% to 25.0%" in prompter.full_output

    def test_already_balanced_reports_no_trades_needed(self, tmp_path):
        config_path = tmp_path / "config.json"
        responses = [
            "100", "y",
            "0",  # rebalancing band -- exact target, as before it existed
            "0",  # ...and its relative half, which zero already settles
            "y",
            *new_account_responses("1", "Roth", "10000", "0", "0"),
            "n",
            "n",  # don't save
        ]
        prompter = ScriptedPrompter(responses)
        exit_code = run(
            ["--config", str(config_path), "--vt-us-pct", "100"], prompter=prompter
        )
        assert exit_code == 0
        assert "already matches your target allocation" in prompter.full_output
        assert not config_path.exists()

    def test_no_save_flag_skips_save_prompt_and_file(self, tmp_path):
        config_path = tmp_path / "config.json"
        responses = [
            "100", "y",
            "0",  # rebalancing band -- exact target, as before it existed
            "0",  # ...and its relative half, which zero already settles
            "y",
            *new_account_responses("1", "Roth", "10000", "0", "0"),
            "n",
            # no response for a save prompt -- it must not be asked
        ]
        prompter = ScriptedPrompter(responses)
        exit_code = run(
            ["--config", str(config_path), "--vt-us-pct", "100", "--no-save"], prompter=prompter
        )
        assert exit_code == 0
        assert prompter.all_consumed()  # save prompt was never asked
        assert not config_path.exists()

    def test_no_accounts_entered_exits_cleanly(self, tmp_path):
        config_path = tmp_path / "config.json"
        responses = ["100", "y", "0", "0", "n"]  # both band halves, then no accounts
        prompter = ScriptedPrompter(responses)
        exit_code = run(
            ["--config", str(config_path), "--vt-us-pct", "100"], prompter=prompter
        )
        assert exit_code == 0
        assert "nothing to rebalance" in prompter.full_output

    def test_infeasible_target_reports_error_and_exits_nonzero(self, tmp_path):
        """A target-date account is the only thing that can still pin an
        asset class out of reach: an account holding individual funds
        declares all three, so it can always be traded to any mix."""
        config_path = tmp_path / "config.json"
        responses = [
            "50", "y",  # a 50% bond target
            "0",  # rebalancing band -- exact target, as before it existed
            "0",  # ...and its relative half, which zero already settles
            "y",
            "1", "Roth",
            "2",  # a single target-date fund, held whole
            "All-Stock 2065", "10000",
            "100", "0", "y",  # ...that holds no bonds at all
            "0",
            "n",
        ]
        prompter = ScriptedPrompter(responses)
        exit_code = run(
            ["--config", str(config_path), "--vt-us-pct", "100"], prompter=prompter
        )
        assert exit_code == 1
        assert "Could not compute a rebalance" in prompter.full_output

    def test_corrupt_config_file_falls_back_to_blank_with_warning(self, tmp_path):
        config_path = tmp_path / "config.json"
        config_path.write_text("{not valid json")
        responses = [
            "100", "y",
            "0",  # rebalancing band -- exact target, as before it existed
            "0",  # ...and its relative half, which zero already settles
            "n",  # no accounts, just check it doesn't crash
        ]
        prompter = ScriptedPrompter(responses)
        exit_code = run(
            ["--config", str(config_path), "--vt-us-pct", "100"], prompter=prompter
        )
        assert exit_code == 0
        assert "could not read your saved portfolio" in prompter.full_output.lower()

    def test_wrongly_shaped_config_falls_back_to_blank_with_warning(self, tmp_path):
        """Valid JSON, wrong shape -- the file parses, so the failure happens
        deeper than json.loads. It still has to be recoverable."""
        config_path = tmp_path / "config.json"
        config_path.write_text('{"schema_version": 2, "accounts": ["not an account"]}')
        prompter = ScriptedPrompter(["100", "y", "0", "0", "n"])
        exit_code = run(
            ["--config", str(config_path), "--vt-us-pct", "100"], prompter=prompter
        )
        assert exit_code == 0
        assert "could not read your saved portfolio" in prompter.full_output.lower()

    def test_fresh_flag_ignores_existing_config(self, tmp_path):
        config_path = tmp_path / "config.json"
        # First run: create and save a config with one account.
        first_responses = [
            "100", "y",
            "0",  # rebalancing band -- exact target, as before it existed
            "0",  # ...and its relative half, which zero already settles
            "y",
            *new_account_responses("1", "Roth", "10000", "0", "0"),
            "n", "y",
        ]
        run(
            ["--config", str(config_path), "--vt-us-pct", "100"],
            prompter=ScriptedPrompter(first_responses),
        )
        assert config_path.exists()

        # Second run with --fresh should not see the saved account at all,
        # i.e. it should prompt to add a fresh one rather than offer to keep it.
        second_responses = ["100", "y", "0", "0", "n"]
        prompter = ScriptedPrompter(second_responses)
        exit_code = run(
            ["--config", str(config_path), "--vt-us-pct", "100", "--fresh"], prompter=prompter
        )
        assert exit_code == 0
        assert "Keep account" not in prompter.full_output


class TestLongMessagesWrap:
    """Messages that interpolate a path, a URL or an exception have no length
    of their own. The rebalance error was the worst: roughly 250 characters
    on one line."""

    def _unwrapped(self, prompter) -> list[str]:
        """Lines that overrun and *could* have been broken. A line carrying a
        single token longer than the page -- a URL, or pytest's own tmp_path --
        is not a violation: wrap deliberately never splits a long word, since
        half a URL is worse than a long line."""
        return [
            line
            for block in prompter.output
            for line in block.split("\n")
            if len(line) > prose_width()
            and max((len(word) for word in line.split()), default=0) <= prose_width()
        ]

    def test_a_corrupt_config_warning_wraps(self, tmp_path):
        config_path = tmp_path / "a-fairly-long-config-file-name.json"
        config_path.write_text("{not valid json")
        prompter = ScriptedPrompter(["100", "y", "0", "0", "n"])
        run(["--config", str(config_path), "--vt-us-pct", "100"], prompter=prompter)
        assert "could not read your saved portfolio" in prompter.full_output
        assert self._unwrapped(prompter) == []

    def test_an_infeasible_target_error_wraps(self, tmp_path):
        prompter = ScriptedPrompter([
            "50", "y",  # a bond target the one fund held cannot reach
            "0", "0",
            "y", "1", "Roth", "2",
            "All-Stock 2065", "10000",
            "100", "0", "y",
            "0",
            "n",
        ])
        exit_code = run(
            ["--config", str(tmp_path / "c.json"), "--vt-us-pct", "100"], prompter=prompter
        )
        assert exit_code == 1
        assert "Could not compute a rebalance" in prompter.full_output
        assert self._unwrapped(prompter) == []

    def test_the_manual_vt_prompt_wraps_without_breaking_the_url(self, tmp_path):
        prompter = ScriptedPrompter(["100", "y", "62", "0", "0", "n", "n"])
        run(["--config", str(tmp_path / "c.json"), "--offline"], prompter=prompter)
        output = prompter.full_output
        assert VT_FUND_PAGE_URL in output, "the URL must survive wrapping intact"
        assert self._unwrapped(prompter) == []
