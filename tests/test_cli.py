from decimal import Decimal

import pytest

from three_fund_rebalance import __version__
from three_fund_rebalance.cli import parse_args, run
from three_fund_rebalance.models import FundType
from three_fund_rebalance.persistence import load_config
from three_fund_rebalance.prompts import Prompter


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
    domestic_balance: str,
    intl_balance: str,
    bond_balance: str,
    cash: str = "0",
) -> list[str]:
    return [
        account_type_index,
        nickname,
        "y", "VTI", domestic_balance,
        "y", "VXUS", intl_balance,
        "y", "BND", bond_balance,
        "n",  # TDF
        cash,
    ]


class TestArgParsing:
    def test_invalid_vt_us_pct_exits_with_usage_error(self, capsys):
        with pytest.raises(SystemExit) as exc_info:
            parse_args(["--vt-us-pct", "not-a-number"])
        assert exc_info.value.code == 2
        assert "not a valid number" in capsys.readouterr().err

    def test_version_flag_prints_version_and_exits_cleanly(self, capsys):
        with pytest.raises(SystemExit) as exc_info:
            parse_args(["--version"])
        assert exc_info.value.code == 0
        assert __version__ in capsys.readouterr().out


class TestEndToEndRun:
    def test_run_without_vt_flag_uses_resolve_vt_split_offline_path(self, tmp_path):
        # Without --vt-us-pct, run() should go through resolve_vt_split(); using
        # --offline with no cache exercises the manual-entry fallback without
        # touching the network.
        config_path = tmp_path / "config.json"
        responses = [
            "80", "20",  # stock/bond target
            "75",  # manual VT US % entry (offline, no cache)
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
            "80", "20",  # stock/bond target
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
        # domestic 80*75%=60, intl 20, bond 20 on a $10,000 account starting all-domestic
        assert "Sell $4,000.00 of VTI" in prompter.full_output
        assert "Buy $2,000.00 of VXUS" in prompter.full_output
        assert "Buy $2,000.00 of BND" in prompter.full_output

        saved = load_config(config_path)
        assert saved.stock_pct == Decimal(80)
        assert saved.bond_pct == Decimal(20)
        assert saved.vt_us_pct == Decimal(75)
        assert len(saved.accounts) == 1
        # Persisted balances reflect what the user *entered* (current holdings),
        # not the hypothetical post-trade recommendation.
        assert saved.accounts[0].get_holding(FundType.DOMESTIC_EQUITY).balance == Decimal(10_000)

    def test_already_balanced_reports_no_trades_needed(self, tmp_path):
        config_path = tmp_path / "config.json"
        responses = [
            "100", "0",
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
            "100", "0",
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
        responses = ["100", "0", "n"]  # decline to add any account
        prompter = ScriptedPrompter(responses)
        exit_code = run(
            ["--config", str(config_path), "--vt-us-pct", "100"], prompter=prompter
        )
        assert exit_code == 0
        assert "nothing to rebalance" in prompter.full_output

    def test_infeasible_target_reports_error_and_exits_nonzero(self, tmp_path):
        config_path = tmp_path / "config.json"
        responses = [
            "50", "50",  # 50% bond target
            "y",
            "1", "Roth",
            "y", "VTI", "10000",  # only a domestic equity fund -- no bond slot
            "n", "n", "n",  # no intl, no bond, no TDF
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
            "100", "0",
            "n",  # no accounts, just check it doesn't crash
        ]
        prompter = ScriptedPrompter(responses)
        exit_code = run(
            ["--config", str(config_path), "--vt-us-pct", "100"], prompter=prompter
        )
        assert exit_code == 0
        assert "could not read config" in prompter.full_output.lower()

    def test_fresh_flag_ignores_existing_config(self, tmp_path):
        config_path = tmp_path / "config.json"
        # First run: create and save a config with one account.
        first_responses = [
            "100", "0", "y",
            *new_account_responses("1", "Roth", "10000", "0", "0"),
            "n", "y",
        ]
        run(["--config", str(config_path), "--vt-us-pct", "100"], prompter=ScriptedPrompter(first_responses))
        assert config_path.exists()

        # Second run with --fresh should not see the saved account at all,
        # i.e. it should prompt to add a fresh one rather than offer to keep it.
        second_responses = ["100", "0", "n"]
        prompter = ScriptedPrompter(second_responses)
        exit_code = run(
            ["--config", str(config_path), "--vt-us-pct", "100", "--fresh"], prompter=prompter
        )
        assert exit_code == 0
        assert "Keep account" not in prompter.full_output
