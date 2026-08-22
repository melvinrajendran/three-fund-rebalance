from decimal import Decimal

import pytest

from three_fund_rebalance import prompts as prompts_module
from three_fund_rebalance.models import (
    Account,
    FundType,
    Holding,
    TargetDateAllocation,
    TaxTreatment,
)
from three_fund_rebalance.prompts import (
    Prompter,
    prompt_accounts,
    prompt_choice,
    prompt_decimal,
    prompt_stock_bond_allocation,
    prompt_str,
    prompt_yes_no,
    resolve_vt_allocation,
)
from three_fund_rebalance.vt_allocation import VTAllocationResult, VTFetchError


class TestPrompterIndentation:
    def make(self):
        said, asked = [], []
        prompter = Prompter(
            input_func=lambda text: asked.append(text) or "",
            print_func=said.append,
        )
        return prompter, said, asked

    def test_says_and_asks_at_the_current_depth(self):
        prompter, said, asked = self.make()
        prompter.say("flush")
        with prompter.indented():
            prompter.say("one deep")
            prompter.ask("question: ")
            with prompter.indented():
                prompter.say("two deep")
        prompter.say("flush again")
        assert said == ["flush", "  one deep", "    two deep", "flush again"]
        assert asked == ["  question: "]

    def test_depth_is_restored_even_if_the_block_raises(self):
        prompter, said, _ = self.make()
        with pytest.raises(RuntimeError), prompter.indented():
            raise RuntimeError("boom")
        prompter.say("flush")
        assert said == ["flush"]

    def test_a_leading_blank_line_stays_flush_so_it_still_separates(self):
        """Messages that open with a newline use it as a separator; padding it
        would emit a line of trailing whitespace instead of a blank one."""
        prompter, said, _ = self.make()
        with prompter.indented():
            prompter.say("\nafter a gap")
        assert said == ["\n  after a gap"]

    def test_every_line_of_a_multi_line_message_is_indented(self):
        prompter, said, _ = self.make()
        with prompter.indented():
            prompter.say("first\nsecond")
        assert said == ["  first\n  second"]


class ScriptedPrompter(Prompter):
    """A Prompter driven by a queue of canned responses, for testing the
    interactive flow without a real terminal."""

    def __init__(self, responses: list[str]):
        self._responses = list(responses)
        self.said: list[str] = []
        super().__init__(input_func=self._next, print_func=self.said.append)

    def _next(self, _text: str = "") -> str:
        if not self._responses:
            raise AssertionError("Ran out of scripted responses")
        return self._responses.pop(0)

    def all_consumed(self) -> bool:
        return not self._responses


class TestPromptStr:
    def test_returns_valid_input(self):
        p = ScriptedPrompter(["hello"])
        assert prompt_str(p, "Name") == "hello"

    def test_retries_on_empty_without_default(self):
        p = ScriptedPrompter(["", "", "value"])
        assert prompt_str(p, "Name") == "value"

    def test_uses_default_on_empty(self):
        p = ScriptedPrompter([""])
        assert prompt_str(p, "Name", default="fallback") == "fallback"


class TestPromptDecimal:
    def test_parses_valid_number(self):
        p = ScriptedPrompter(["42.5"])
        assert prompt_decimal(p, "Amount") == Decimal("42.5")

    def test_retries_on_invalid_number(self):
        p = ScriptedPrompter(["abc", "10"])
        assert prompt_decimal(p, "Amount") == Decimal(10)

    def test_enforces_min_and_max(self):
        p = ScriptedPrompter(["-5", "200", "50"])
        assert prompt_decimal(p, "Amount", min_value=Decimal(0), max_value=Decimal(100)) == Decimal(50)

    def test_uses_default_on_empty(self):
        p = ScriptedPrompter([""])
        assert prompt_decimal(p, "Amount", default=Decimal(0)) == Decimal(0)


class TestPromptYesNo:
    @pytest.mark.parametrize("raw,expected", [("y", True), ("yes", True), ("n", False), ("no", False)])
    def test_parses_variants(self, raw, expected):
        p = ScriptedPrompter([raw])
        assert prompt_yes_no(p, "Sure?") == expected

    def test_retries_on_invalid(self):
        p = ScriptedPrompter(["maybe", "y"])
        assert prompt_yes_no(p, "Sure?") is True

    def test_uses_default_on_empty(self):
        p = ScriptedPrompter([""])
        assert prompt_yes_no(p, "Sure?", default=False) is False


class TestPromptChoice:
    def test_selects_by_number(self):
        p = ScriptedPrompter(["2"])
        assert prompt_choice(p, "Pick:", ["A", "B", "C"]) == "B"

    def test_retries_on_out_of_range(self):
        p = ScriptedPrompter(["0", "5", "3"])
        assert prompt_choice(p, "Pick:", ["A", "B", "C"]) == "C"

    def test_uses_default_on_empty(self):
        p = ScriptedPrompter([""])
        assert prompt_choice(p, "Pick:", ["A", "B", "C"], default="B") == "B"


class TestPromptStockBondTarget:
    def test_accepts_valid_allocation(self):
        p = ScriptedPrompter(["80", "20"])
        stock, bond = prompt_stock_bond_allocation(p)
        assert (stock, bond) == (Decimal(80), Decimal(20))

    def test_retries_when_not_summing_to_100(self):
        p = ScriptedPrompter(["80", "30", "70", "30"])
        stock, bond = prompt_stock_bond_allocation(p)
        assert (stock, bond) == (Decimal(70), Decimal(30))


class TestResolveVtSplit:
    def test_uses_live_fetch_when_accepted(self, monkeypatch):
        monkeypatch.setattr(
            prompts_module,
            "fetch_vt_us_pct",
            lambda: VTAllocationResult(us_pct=Decimal("61.9"), as_of="June 30, 2026", source="vanguard_fact_sheet"),
        )
        p = ScriptedPrompter(["y"])
        result = resolve_vt_allocation(p)
        assert result.us_pct == Decimal("61.9")
        assert result.source == "vanguard_fact_sheet"

    def test_falls_back_to_cache_when_live_value_rejected(self, monkeypatch):
        monkeypatch.setattr(
            prompts_module,
            "fetch_vt_us_pct",
            lambda: VTAllocationResult(us_pct=Decimal("61.9"), as_of="June 30, 2026", source="vanguard_fact_sheet"),
        )
        p = ScriptedPrompter(["n", "y"])  # reject live, accept cache
        result = resolve_vt_allocation(p, cached_us_pct=Decimal(60), cached_as_of="last quarter")
        assert result.us_pct == Decimal(60)
        assert result.source == "cache"

    def test_falls_back_to_cache_when_fetch_fails(self, monkeypatch):
        def raise_fetch_error():
            raise VTFetchError("network down")

        monkeypatch.setattr(prompts_module, "fetch_vt_us_pct", raise_fetch_error)
        p = ScriptedPrompter(["y"])  # accept cache
        result = resolve_vt_allocation(p, cached_us_pct=Decimal(60), cached_as_of="last quarter")
        assert result.us_pct == Decimal(60)
        assert result.source == "cache"

    def test_falls_back_to_manual_when_fetch_fails_and_no_cache(self, monkeypatch):
        def raise_fetch_error():
            raise VTFetchError("network down")

        monkeypatch.setattr(prompts_module, "fetch_vt_us_pct", raise_fetch_error)
        p = ScriptedPrompter(["58"])
        result = resolve_vt_allocation(p)
        assert result.us_pct == Decimal(58)
        assert result.source == "manual"

    def test_offline_skips_fetch_entirely(self, monkeypatch):
        def fail_if_called():
            raise AssertionError("should not fetch when offline")

        monkeypatch.setattr(prompts_module, "fetch_vt_us_pct", fail_if_called)
        p = ScriptedPrompter(["y"])
        result = resolve_vt_allocation(p, cached_us_pct=Decimal(60), cached_as_of="last quarter", offline=True)
        assert result.us_pct == Decimal(60)


class TestPromptAccounts:
    def test_add_one_new_account_with_three_funds(self):
        responses = [
            "y",  # Add an account?
            "1",  # account type -> Roth IRA
            "My Roth",  # nickname
            "1",  # holds individual funds
            "y", "VTI", "6000",  # U.S. stock fund
            "y", "VXUS", "2000",  # international stock fund
            "y", "BND", "2000",  # bond
            "0",  # cash
            "n",  # Add another account?
        ]
        p = ScriptedPrompter(responses)
        accounts = prompt_accounts(p, [])
        assert p.all_consumed()
        assert len(accounts) == 1
        account = accounts[0]
        assert account.name == "My Roth"
        assert account.account_type == "Roth IRA"
        assert account.tax_treatment == TaxTreatment.TAX_FREE
        assert account.total_value() == Decimal(10_000)
        assert account.get_holding(FundType.US_STOCK).name == "VTI"

    def test_duplicate_nickname_is_rejected_and_retried(self):
        responses = [
            "y", "1", "First", "1", "n", "n", "n", "0",
            "y", "1", "First", "SecondUnique", "1", "n", "n", "n", "0",
            "n",
        ]
        p = ScriptedPrompter(responses)
        accounts = prompt_accounts(p, [])
        assert [a.name for a in accounts] == ["First", "SecondUnique"]

    def test_target_date_allocation_must_sum_to_100_with_retry(self):
        responses = [
            "y", "1", "401k",
            "2",  # holds a single target-date fund
            "Target 2050", "10000",
            "50", "30", "10",  # invalid sum (90)
            "60", "20", "20",  # valid
            "0",  # cash
            "n",
        ]
        p = ScriptedPrompter(responses)
        accounts = prompt_accounts(p, [])
        target_date_holding = accounts[0].get_holding(FundType.TARGET_DATE)
        assert target_date_holding.target_date_allocation.us_stock_pct == Decimal(60)

    def test_keep_existing_account_and_update_value_via_default(self):
        existing = Account(
            account_type="Roth IRA",
            name="My Roth",
            tax_treatment=TaxTreatment.TAX_DEFERRED,
            holdings=[
                Holding(fund_type=FundType.US_STOCK, name="VTI", value=Decimal(6000)),
            ],
        )
        responses = [
            "y",  # Keep account 'My Roth'?
            "",  # value default (keep 6000)
            "n", "n",  # decline adding international/bond
            "",  # cash default (0)
            "n",  # Add another account?
        ]
        p = ScriptedPrompter(responses)
        accounts = prompt_accounts(p, [existing])
        assert len(accounts) == 1
        assert accounts[0].get_holding(FundType.US_STOCK).value == Decimal(6000)

    def test_removing_existing_account(self):
        existing = Account(
            account_type="Roth IRA",
            name="My Roth",
            tax_treatment=TaxTreatment.TAX_DEFERRED,
            holdings=[],
        )
        responses = [
            "n",  # Keep account 'My Roth'? -> no, remove it
            "n",  # Add an account? -> no
        ]
        p = ScriptedPrompter(responses)
        accounts = prompt_accounts(p, [existing])
        assert accounts == []

    def _other_account_responses(self, tax_treatment_choice: str) -> list[str]:
        return [
            "y",  # Add an account?
            "11",  # "Other" is the last entry in ACCOUNT_TYPE_CHOICES
            tax_treatment_choice,  # how is this account taxed?
            "MyOtherAccount",
            "1",  # holds individual funds
            "n", "n", "n",  # decline all three of them
            "50",  # nonzero cash
            "n",  # Add another account?
        ]

    def test_other_account_type_asks_tax_treatment_explicitly(self):
        p = ScriptedPrompter(self._other_account_responses("2"))
        accounts = prompt_accounts(p, [])
        assert accounts[0].account_type == "Other"
        assert accounts[0].tax_treatment == TaxTreatment.TAX_DEFERRED
        assert accounts[0].available_cash() == Decimal(50)

    def test_other_account_type_can_be_declared_tax_free(self):
        """The two shelters are asked apart, not lumped into one yes/no --
        which one an unrecognized account is decides whether bonds belong
        there."""
        p = ScriptedPrompter(self._other_account_responses("3"))
        accounts = prompt_accounts(p, [])
        assert accounts[0].tax_treatment == TaxTreatment.TAX_FREE

    def test_other_account_type_can_be_declared_taxable(self):
        p = ScriptedPrompter(self._other_account_responses("1"))
        accounts = prompt_accounts(p, [])
        assert accounts[0].tax_treatment == TaxTreatment.TAXABLE

    def test_updating_an_individual_fund_account_offers_only_the_missing_ones(self):
        existing = Account(
            account_type="Roth 401(k)",
            name="401k",
            tax_treatment=TaxTreatment.TAX_DEFERRED,
            holdings=[
                Holding(fund_type=FundType.US_STOCK, name="VTI", value=Decimal(6000)),
                Holding(fund_type=FundType.CASH, name="", value=Decimal(100)),
            ],
        )
        responses = [
            "y",  # Keep account '401k'?
            "",  # VTI value -> keep default 6000
            "y", "VXUS", "500",  # add international stock fund (not previously declared)
            "n",  # decline adding a U.S. bond fund
            "200",  # cash -> update to 200
            "n",  # Add another account?
        ]
        p = ScriptedPrompter(responses)
        accounts = prompt_accounts(p, [existing])
        assert p.all_consumed()
        updated = accounts[0]
        assert updated.get_holding(FundType.INTERNATIONAL_STOCK).value == Decimal(500)
        assert updated.available_cash() == Decimal(200)
        # A target-date fund was never offered -- this account holds individual
        # funds, so adding one would make it a mix.
        assert updated.get_holding(FundType.TARGET_DATE) is None

    def test_updating_a_cash_only_account_asks_which_kind_it_is_now(self):
        """A saved account with no funds hasn't committed to either kind yet,
        so it gets the same question a brand new account does."""
        existing = Account(
            account_type="Roth IRA",
            name="Empty Roth",
            tax_treatment=TaxTreatment.TAX_DEFERRED,
            holdings=[Holding(fund_type=FundType.CASH, name="", value=Decimal(500))],
        )
        responses = [
            "y",  # Keep account 'Empty Roth'?
            "2",  # it now holds a single target-date fund
            "Target 2050", "500",
            "60", "20", "20",
            "0",  # cash -> all invested
            "n",  # Add another account?
        ]
        p = ScriptedPrompter(responses)
        accounts = prompt_accounts(p, [existing])
        assert p.all_consumed()
        assert accounts[0].get_holding(FundType.TARGET_DATE).value == Decimal(500)
        assert accounts[0].available_cash() == Decimal(0)

    def test_updating_a_target_date_account_offers_no_other_funds(self):
        existing = Account(
            account_type="Roth 401(k)",
            name="401k",
            tax_treatment=TaxTreatment.TAX_DEFERRED,
            holdings=[
                Holding(
                    fund_type=FundType.TARGET_DATE,
                    name="Target 2050",
                    value=Decimal(3000),
                    target_date_allocation=TargetDateAllocation(
                        us_stock_pct=Decimal(60),
                        international_stock_pct=Decimal(20),
                        bond_pct=Decimal(20),
                    ),
                ),
                Holding(fund_type=FundType.CASH, name="", value=Decimal(100)),
            ],
        )
        responses = [
            "y",  # Keep account '401k'?
            "",  # target-date fund value -> keep default 3000
            "y",  # update the fund's underlying allocation?
            "70", "15", "15",  # new underlying allocation
            "200",  # cash -> update to 200
            "n",  # Add another account?
        ]
        p = ScriptedPrompter(responses)
        accounts = prompt_accounts(p, [existing])
        assert p.all_consumed()
        updated = accounts[0]
        assert updated.get_holding(FundType.TARGET_DATE).target_date_allocation.us_stock_pct == Decimal(70)
        assert updated.available_cash() == Decimal(200)
        assert [h.fund_type for h in updated.holdings] == [FundType.TARGET_DATE, FundType.CASH]
        # The mix being replaced is shown, so the answer isn't from memory. It
        # is the *old* one: the question hasn't been answered yet at that point.
        assert any(
            "Currently 60% U.S. stocks / 20% international stocks / 20% bonds" in line
            for line in p.said
        )

    def test_taxable_account_with_bonds_prints_a_note(self):
        responses = [
            "y", "10",  # account type "Taxable Brokerage" is index 10 in ACCOUNT_TYPE_CHOICES
            "Brokerage",
            "1",  # holds individual funds
            "n", "n",  # no U.S./international stock funds
            "y", "BND", "1000",  # bond fund
            "0",  # cash
            "n",
        ]
        p = ScriptedPrompter(responses)
        prompt_accounts(p, [])
        assert any("bonds" in line.lower() and "taxed" in line.lower() for line in p.said)
