from decimal import Decimal

import pytest

from three_fund_rebalance import prompts as prompts_module
from three_fund_rebalance.formatting import prose_width, wrap
from three_fund_rebalance.models import (
    Account,
    FundType,
    Holding,
    TargetDateAllocation,
    TaxTreatment,
)
from three_fund_rebalance.prompts import (
    BAND_EXPLANATION,
    FUND_EXPLANATION,
    Prompter,
    _prompt_target_date_allocation,
    prompt_accounts,
    prompt_choice,
    prompt_decimal,
    prompt_rebalance_band,
    prompt_relative_rebalance_band,
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

    @property
    def text(self) -> str:
        return "\n".join(self.said)


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

    def test_a_value_typed_at_a_fund_name_prompt_is_refused(self):
        """The slip this exists for. On a saved account the ticker arrives
        pre-filled and the value is the only thing that changed, so typing
        the new value at the name prompt is the natural mistake -- and
        without this the amount silently becomes the fund's name, is saved,
        and comes back in the plan as "Buy $29,500.00 of 178000"."""
        p = ScriptedPrompter(["178000", "VTI"])
        assert prompt_str(p, "U.S. stock fund", default="VTI", reject_numeric=True) == "VTI"
        assert "looks like a number" in p.text

    def test_anything_the_value_prompt_would_have_taken_is_refused(self):
        """Stated as "what the other question accepts" rather than as a
        pattern of digits, so the two cannot drift apart."""
        for typed in ("0", "-5", "1234.56", "1.5e5"):
            p = ScriptedPrompter([typed, "VTI"])
            assert prompt_str(p, "Bond fund", reject_numeric=True) == "VTI", typed

    def test_a_fund_name_carrying_digits_is_still_accepted(self):
        """Only a bare number is refused. Real funds are full of digits --
        a target-date year, an index's number, a share class."""
        for name in ("Target 2050", "500 Index", "FXAIX", "VTI"):
            p = ScriptedPrompter([name])
            assert prompt_str(p, "U.S. stock fund", reject_numeric=True) == name, name

    def test_an_account_nickname_may_still_be_a_number(self):
        """The guard is asked for at the fund prompts and nowhere else -- a
        nickname sits next to no value question, and it is a label the user
        invents rather than one an order is placed against."""
        p = ScriptedPrompter(["401"])
        assert prompt_str(p, "Account nickname") == "401"


class TestPromptDecimal:
    def test_parses_valid_number(self):
        p = ScriptedPrompter(["42.5"])
        assert prompt_decimal(p, "Amount") == Decimal("42.5")

    def test_retries_on_invalid_number(self):
        p = ScriptedPrompter(["abc", "10"])
        assert prompt_decimal(p, "Amount") == Decimal(10)

    def test_enforces_min_and_max(self):
        p = ScriptedPrompter(["-5", "200", "50"])
        amount = prompt_decimal(p, "Amount", min_value=Decimal(0), max_value=Decimal(100))
        assert amount == Decimal(50)

    def test_uses_default_on_empty(self):
        p = ScriptedPrompter([""])
        assert prompt_decimal(p, "Amount", default=Decimal(0)) == Decimal(0)


class TestPromptYesNo:
    @pytest.mark.parametrize(
        "raw,expected", [("y", True), ("yes", True), ("n", False), ("no", False)]
    )
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


class TestAndList:
    """The saved-accounts line is a sentence, so the names in it are joined as
    prose rather than as a bare comma list."""

    def test_one_name_stands_alone(self):
        assert prompts_module._and_list(["A"]) == "A"

    def test_two_names_take_no_comma(self):
        """The serial comma separates three or more; "A, and B" reads as a
        stray one."""
        assert prompts_module._and_list(["A", "B"]) == "A and B"

    def test_three_or_more_take_the_serial_comma(self):
        assert prompts_module._and_list(["A", "B", "C"]) == "A, B, and C"
        assert prompts_module._and_list(["A", "B", "C", "D"]) == "A, B, C, and D"


class TestRebalanceBandPrompts:
    """The relative half is the one question in the flow nobody can answer
    from its own label, so this pair is the exception to asking bare."""

    def _asked(self, func):
        asked = []
        p = Prompter(input_func=lambda text: asked.append(text) or "5", print_func=lambda _: None)
        func(p)
        return asked[0]

    def test_the_two_halves_carry_the_industry_names_for_them(self):
        """The same words `rebalance_band_pct` and
        `rebalance_relative_band_pct` are named after, so the question, the
        saved key and the report all say one thing."""
        assert self._asked(prompt_rebalance_band).startswith("Absolute band,")
        assert self._asked(prompt_relative_rebalance_band).startswith("Relative band,")

    def test_each_half_names_its_own_unit(self):
        """Points of the whole portfolio against a share of one class's
        target. Asking for "5 (%)" of drift next to "25 (%)" of a target is
        how they get read as the same kind of number."""
        assert "(pts)" in self._asked(prompt_rebalance_band)
        assert "(%)" in self._asked(prompt_relative_rebalance_band)
        assert "(%)" not in self._asked(prompt_rebalance_band)

    def test_the_explanation_states_the_policy_the_way_one_is_written(self):
        """An IPS says a class "drifts from its target" by more than "the
        smaller of" two bands. Carrying the semantics here is what lets each
        question below name only its own unit."""
        assert "drifts from its target" in BAND_EXPLANATION
        assert "the smaller of" in BAND_EXPLANATION

    def test_the_explanation_is_one_sentence(self):
        """It exists to make the two questions answerable, not to teach the
        band -- the report's own section shows its effect."""
        assert BAND_EXPLANATION.count(".") == 1
        assert len(wrap(BAND_EXPLANATION).split("\n")) <= 2

    def test_neither_half_offers_a_suggested_answer(self):
        """The band decides whether the program does anything at all, and 5/25
        is a convention rather than a recommendation this program is in a
        position to make. Offering it meant a first run could be walked past
        with two keystrokes, and a number the user never chose then reads back
        in the report as their own policy."""
        assert "[" not in self._asked(prompt_rebalance_band)
        assert "[" not in self._asked(prompt_relative_rebalance_band)

    def test_pressing_enter_re_asks_rather_than_choosing_for_you(self):
        p = ScriptedPrompter(["", "5"])
        assert prompt_rebalance_band(p) == Decimal(5)
        assert "Please enter a number." in p.said

    def test_a_saved_answer_is_still_offered_as_an_editable_default(self):
        """Nothing about requiring an answer changes the persistence contract:
        a returning user presses Enter to keep what they chose last time."""
        p = ScriptedPrompter([""])
        assert prompt_rebalance_band(p, default=Decimal(3)) == Decimal(3)
        p = ScriptedPrompter([""])
        assert prompt_relative_rebalance_band(p, default=Decimal(20)) == Decimal(20)


class TestTaxTreatmentChoices:
    """Asked only for an account type we don't recognize, and worded by when
    the tax is paid rather than by the category name -- "tax-deferred" versus
    "tax-free" is exactly the distinction someone picking "Other" may not
    have the vocabulary for, and it is the one that decides where bonds go."""

    def test_each_choice_fits_the_page_width(self):
        """prompt_choice prints its options unwrapped, so one that runs long
        wraps at the terminal and strands its own tail in the next option's
        column."""
        for i, choice in enumerate(prompts_module._TAX_TREATMENT_BY_CHOICE, start=1):
            assert len(f"  {i}. {choice}") <= prose_width(), choice

    def test_each_choice_says_when_the_tax_is_paid(self):
        by_treatment = {
            treatment: choice
            for choice, treatment in prompts_module._TAX_TREATMENT_BY_CHOICE.items()
        }
        # Gains in a taxable account are taxed on sale, not annually -- the
        # earlier wording said "dividends and gains every year".
        assert "gains taxed when I sell" in by_treatment[TaxTreatment.TAXABLE]
        assert "taxed on withdrawal" in by_treatment[TaxTreatment.TAX_DEFERRED]
        assert "qualified withdrawals untaxed" in by_treatment[TaxTreatment.TAX_FREE]


class TestPromptStockBondTarget:
    def test_only_the_stock_share_is_asked_for(self):
        """Two percentages summing to 100 carry one degree of freedom, so the
        bond share is stated back rather than asked for."""
        p = ScriptedPrompter(["80", "y"])
        stock, bond = prompt_stock_bond_allocation(p)
        assert (stock, bond) == (Decimal(80), Decimal(20))
        assert p.all_consumed()

    def test_the_derived_bond_share_is_confirmed_in_words(self):
        asked = []
        answers = iter(["80", "y"])
        p = Prompter(
            input_func=lambda text: asked.append(text) or next(answers),
            print_func=lambda _: None,
        )
        prompt_stock_bond_allocation(p)
        assert any(
            "That leaves a target bond allocation of 20%. Use this value?" in text
            for text in asked
        )

    def test_declining_the_derived_share_restarts_from_the_stock_question(self):
        """The number the user wants to change is the one they typed -- the
        derived half is not theirs to edit -- so a denial goes back to the
        top rather than asking for bonds directly."""
        p = ScriptedPrompter(["80", "n", "70", "y"])
        stock, bond = prompt_stock_bond_allocation(p)
        assert (stock, bond) == (Decimal(70), Decimal(30))


class TestTargetDateAllocationPrompt:
    def _asked(self, answers):
        asked = []
        responses = iter(answers)
        p = Prompter(
            input_func=lambda text: asked.append(text) or next(responses),
            print_func=lambda _: None,
        )
        return _prompt_target_date_allocation(p), asked

    def test_the_derived_sleeve_is_confirmed_as_one_value(self):
        allocation, asked = self._asked(["64", "34.3", "y"])
        assert allocation.bond_pct == Decimal("1.7")
        assert any("That leaves 1.7% bonds. Use this value?" in text for text in asked)

    def test_a_sleeve_the_answers_have_already_settled_is_not_asked_for(self):
        """100% U.S. stocks leaves nothing for either of the other two, so
        both are stated together rather than one being asked for and the
        other derived. The noun agrees with how many are shown."""
        allocation, asked = self._asked(["100", "y"])
        assert (allocation.international_stock_pct, allocation.bond_pct) == (Decimal(0), Decimal(0))
        assert not any("International stocks" in text for text in asked)
        assert any(
            "That leaves 0% international stocks and 0% bonds. Use these values?" in text
            for text in asked
        )


class TestResolveVtSplit:
    def test_uses_live_fetch_when_accepted(self, monkeypatch):
        monkeypatch.setattr(
            prompts_module,
            "fetch_vt_us_pct",
            lambda: VTAllocationResult(
                us_pct=Decimal("61.9"), as_of="June 30, 2026", source="vanguard_fact_sheet"
            ),
        )
        p = ScriptedPrompter(["y"])
        result = resolve_vt_allocation(p)
        assert result.us_pct == Decimal("61.9")
        assert result.source == "vanguard_fact_sheet"

    def test_the_fetched_split_is_named_in_full_with_its_date(self, monkeypatch):
        """Both halves, so the reader does not do the subtraction, and the
        fund spelled out the first time it is named."""
        monkeypatch.setattr(
            prompts_module,
            "fetch_vt_us_pct",
            lambda: VTAllocationResult(
                us_pct=Decimal("62.0"), as_of="2026-07-31", source="vanguard_api"
            ),
        )
        p = ScriptedPrompter(["y"])
        resolve_vt_allocation(p)
        said = " ".join(" ".join(p.said).split())
        assert "Vanguard Total World Stock ETF's (VT) current" in said
        assert "Found 62% U.S. stocks and 38% international stocks (as of July 31, 2026)." in said

    def test_the_saved_split_is_offered_as_saved_rather_than_as_cached(self, monkeypatch):
        def raise_fetch_error():
            raise VTFetchError("network down")

        monkeypatch.setattr(prompts_module, "fetch_vt_us_pct", raise_fetch_error)
        p = ScriptedPrompter(["y"])
        resolve_vt_allocation(p, cached_us_pct=Decimal(60), cached_as_of="2026-06-30")
        said = " ".join(" ".join(p.said).split())
        assert "Last saved: 60% U.S. stocks and 40% international stocks" in said
        assert "(as of June 30, 2026)" in said
        assert "cached" not in said

    def test_the_fund_is_spelled_out_once_however_the_run_reaches_it(self, monkeypatch):
        """--offline never prints the lookup line, so the manual prompt is
        the first thing to name the fund -- and the only thing to expand it."""
        p = ScriptedPrompter(["58"])
        resolve_vt_allocation(p, offline=True)
        said = " ".join(" ".join(p.said).split())
        assert said.count("Vanguard Total World Stock ETF's (VT)") == 1

    def test_falls_back_to_cache_when_live_value_rejected(self, monkeypatch):
        monkeypatch.setattr(
            prompts_module,
            "fetch_vt_us_pct",
            lambda: VTAllocationResult(
                us_pct=Decimal("61.9"), as_of="June 30, 2026", source="vanguard_fact_sheet"
            ),
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
        result = resolve_vt_allocation(
            p, cached_us_pct=Decimal(60), cached_as_of="last quarter", offline=True
        )
        assert result.us_pct == Decimal(60)


class TestSavedAccountsLine:
    def _saved(self, names):
        return [
            Account(
                account_type="Roth IRA",
                name=name,
                tax_treatment=TaxTreatment.TAX_FREE,
                holdings=[Holding(fund_type=FundType.US_STOCK, name="VTI", value=Decimal(1))],
            )
            for name in names
        ]

    def _run(self, names):
        # Kept, then a ticker and a value per asset class plus its cash. The
        # two slots it has never held have no saved name to press Enter on.
        per_account = ["y", "", "", "VXUS", "0", "BND", "0", ""]
        p = ScriptedPrompter([*(per_account * len(names)), "n"])
        prompt_accounts(p, self._saved(names))
        assert p.all_consumed()
        return p

    def test_the_saved_accounts_are_listed_one_per_line(self):
        """A vertical list, not a sentence: these are the headings the
        questions below arrive in, so they are read down the page."""
        output = self._run(["Alpha", "Beta", "Gamma"]).text
        assert "You have 3 saved accounts:" in output
        assert "\n  Alpha\n  Beta\n  Gamma\n" in output

    def test_a_single_saved_account_reads_in_the_singular(self):
        assert "You have 1 saved account:" in self._run(["Alpha"]).text

    def test_the_instruction_is_said_once_above_the_list(self):
        """Not once per account: it is the same instruction for every account
        in the list, so repeating it at the head of each one says nothing the
        account above it has not already said."""
        output = self._run(["Alpha", "Beta"]).text
        assert output.count("press Enter to use its saved value") == 1

    def test_every_kept_account_comes_back(self):
        p = ScriptedPrompter([*(["y", "", "", "VXUS", "0", "BND", "0", ""] * 2), "n"])
        accounts = prompt_accounts(p, self._saved(["Alpha", "Beta"]))
        assert [a.name for a in accounts] == ["Alpha", "Beta"]


class TestPromptAccounts:
    def test_add_one_new_account_with_three_funds(self):
        responses = [
            "y",  # Add an account?
            "1",  # account type -> Roth IRA
            "My Roth",  # nickname
            "1",  # three individual funds
            "VTI", "6000",  # U.S. stock fund
            "VXUS", "2000",  # international stock fund
            "BND", "2000",  # bond fund
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
            "y", "1", "First", "1", "VTI", "0", "VXUS", "0", "BND", "0", "0",
            "y", "1", "First", "SecondUnique",
            "1", "VTI", "0", "VXUS", "0", "BND", "0", "0",
            "n",
        ]
        p = ScriptedPrompter(responses)
        accounts = prompt_accounts(p, [])
        assert [a.name for a in accounts] == ["First", "SecondUnique"]

    def test_target_date_sleeves_over_100_are_rejected_and_retried(self):
        """Only two sleeves are asked for -- the third is what they leave --
        so the one way to be wrong is for the two to exceed 100 outright."""
        responses = [
            "y", "1", "401k",
            "2",  # holds a single target-date fund
            "Target 2050", "10000",
            "50", "60",  # 110 between them, leaving less than no bonds
            "60", "20", "y",  # valid, and the derived 20% bonds confirmed
            "0",  # cash
            "n",
        ]
        p = ScriptedPrompter(responses)
        accounts = prompt_accounts(p, [])
        assert p.all_consumed()
        allocation = accounts[0].get_holding(FundType.TARGET_DATE).target_date_allocation
        assert allocation.us_stock_pct == Decimal(60)
        assert allocation.bond_pct == Decimal(20)

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
            "",  # U.S. stock fund name -> keep VTI
            "",  # ...and its value -> keep 6000
            "VXUS", "0",  # a config saved before all three were asked for
            "BND", "0",
            "",  # cash default (0)
            "n",  # Add another account?
        ]
        p = ScriptedPrompter(responses)
        accounts = prompt_accounts(p, [existing])
        assert p.all_consumed()
        assert len(accounts) == 1
        assert accounts[0].get_holding(FundType.US_STOCK).value == Decimal(6000)

    def test_a_saved_ticker_is_offered_as_an_editable_default(self):
        """A fund's name was fixed once saved. With a slot standing open for
        a fund not yet bought, the name is the part most likely to change --
        a plan swaps its bond fund -- so it is re-asked with the old one
        pre-filled. That is also why no slot ever needs deleting."""
        existing = Account(
            account_type="Traditional 401(k)",
            name="401k",
            tax_treatment=TaxTreatment.TAX_DEFERRED,
            holdings=[
                Holding(fund_type=FundType.US_STOCK, name="VTI", value=Decimal(6000)),
                Holding(fund_type=FundType.INTERNATIONAL_STOCK, name="VXUS", value=Decimal(0)),
                Holding(fund_type=FundType.US_BOND, name="BND", value=Decimal(0)),
            ],
        )
        responses = [
            "y",  # Keep account '401k'?
            "", "",  # U.S. stock fund unchanged
            "", "",  # international unchanged
            "VBTLX", "",  # the plan's bond fund changed; the value did not
            "",  # cash
            "n",
        ]
        p = ScriptedPrompter(responses)
        accounts = prompt_accounts(p, [existing])
        assert p.all_consumed()
        assert accounts[0].get_holding(FundType.US_BOND).name == "VBTLX"
        assert accounts[0].get_holding(FundType.US_BOND).value == Decimal(0)

    def test_a_saved_account_holding_nothing_is_asked_as_though_it_were_new(self):
        """An account that never committed to either kind of holding has no
        slots to pre-fill, so it gets the same question a new account does."""
        existing = Account(
            account_type="Roth IRA",
            name="My Roth",
            tax_treatment=TaxTreatment.TAX_DEFERRED,
            holdings=[],
        )
        responses = [
            "y",  # Keep account 'My Roth'?
            "1",  # three individual funds, as a new account is asked
            "VTI", "1000", "VXUS", "0", "BND", "0",
            "0",  # cash
            "n",  # Add another account?
        ]
        p = ScriptedPrompter(responses)
        accounts = prompt_accounts(p, [existing])
        assert p.all_consumed()
        assert accounts[0].name == "My Roth"
        assert accounts[0].get_holding(FundType.US_STOCK).name == "VTI"

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
            "1",  # three individual funds
            "VTI", "0", "VXUS", "0", "BND", "0",  # declared, none held yet
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

    def test_updating_an_individual_fund_account_asks_for_all_three_slots(self):
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
            "", "",  # U.S. stock fund -> keep VTI at 6000
            "VXUS", "500",  # international, never declared before
            "BND", "0",  # ...and a bond slot standing open
            "200",  # cash -> update to 200
            "n",  # Add another account?
        ]
        p = ScriptedPrompter(responses)
        accounts = prompt_accounts(p, [existing])
        assert p.all_consumed()
        updated = accounts[0]
        assert updated.get_holding(FundType.INTERNATIONAL_STOCK).value == Decimal(500)
        assert updated.get_holding(FundType.US_BOND).value == Decimal(0)
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
            "60", "20", "y",
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
            "",  # target-date fund name -> keep 'Target 2050'
            "",  # ...and its value -> keep default 3000
            "y",  # update the fund's underlying allocation?
            "70", "15", "y",  # new underlying allocation; 15% bonds derived
            "200",  # cash -> update to 200
            "n",  # Add another account?
        ]
        p = ScriptedPrompter(responses)
        accounts = prompt_accounts(p, [existing])
        assert p.all_consumed()
        updated = accounts[0]
        allocation = updated.get_holding(FundType.TARGET_DATE).target_date_allocation
        assert allocation.us_stock_pct == Decimal(70)
        assert updated.available_cash() == Decimal(200)
        assert [h.fund_type for h in updated.holdings] == [FundType.TARGET_DATE, FundType.CASH]
        # The mix being replaced is shown, so the answer isn't from memory. It
        # is the *old* one: the question hasn't been answered yet at that point.
        assert any(
            "Currently 60% U.S. stocks, 20% international stocks, and 20% bonds" in line
            for line in p.said
        )

    def test_a_fund_with_no_position_is_still_declared(self):
        """The point of asking for all three: a fund the user owns none of
        becomes a slot the solver can buy into. Answering "no" to a fund not
        yet bought used to remove the only place an asset class could go."""
        responses = [
            "y", "10",  # account type "Brokerage" is index 10 in ACCOUNT_TYPE_CHOICES
            "Brokerage",
            "1",  # three individual funds
            "VTI", "60000",
            "VXUS", "30000",
            "BND", "",  # named, nothing held yet
            "0",  # cash
            "n",
        ]
        p = ScriptedPrompter(responses)
        accounts = prompt_accounts(p, [])
        assert p.all_consumed()
        bond_slot = accounts[0].get_holding(FundType.US_BOND)
        assert bond_slot.name == "BND"
        assert bond_slot.value == Decimal(0)

    def test_the_fund_questions_are_introduced_once(self):
        """Nothing in "Bond fund:" says a fund you own none of belongs in the
        answer, so the one sentence above the three says it."""
        responses = [
            "y", "1", "My Roth", "1",
            "VTI", "1", "VXUS", "0", "BND", "0", "0",
            "n",
        ]
        p = ScriptedPrompter(responses)
        prompt_accounts(p, [])
        assert sum(FUND_EXPLANATION in text for text in p.said) == 1

    def test_the_asset_location_note_is_not_said_during_onboarding(self):
        """It explained a trade the user had not seen yet. The README's
        "Asset location" entry is where it lives now."""
        responses = [
            "y", "10", "Brokerage", "1",
            "VTI", "0", "VXUS", "0", "BND", "1000", "0",
            "n",
        ]
        p = ScriptedPrompter(responses)
        prompt_accounts(p, [])
        assert not any("taxed yearly as ordinary income" in text for text in p.said)
