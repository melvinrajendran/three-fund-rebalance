from decimal import Decimal

import pytest

from three_fund_rebalance import prompts as prompts_module
from three_fund_rebalance.formatting import prose_width, wrap
from three_fund_rebalance.models import (
    Account,
    FundAllocation,
    FundCatalog,
    FundProfile,
    FundType,
    Holding,
    TaxTreatment,
)
from three_fund_rebalance.prompts import (
    BAND_EXPLANATION,
    FUND_EXPLANATION,
    Prompter,
    _prompt_fund_allocation,
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


#: Which of the four "What does this fund hold?" choices each kind is.
US_STOCK_KIND, INTERNATIONAL_KIND, BOND_KIND, MULTI_ASSET_KIND = "1", "2", "3", "4"


def fund_responses(name: str, kind: str, value: str) -> list[str]:
    """One fund's answers: name, kind, value."""
    return [name, kind, value]


def known_fund_responses(name: str, value: str, use_saved: str = "") -> list[str]:
    """A fund whose name the catalog already knows: its name, "Use these
    details?" (default yes), and its value."""
    return [name, use_saved, value]


def multi_asset_fund_responses(
    name: str, value: str, us_stock: str = "60", international: str = "20"
) -> list[str]:
    """One multi-asset fund, its mix asked between the kind and the value and
    its bond sleeve derived from the other two."""
    return [name, MULTI_ASSET_KIND, us_stock, international, "y", value]


def fund_list_responses(*funds: list[str]) -> list[str]:
    """A whole fund list: each fund behind a "y" to "Add another fund?" after
    the first, and one "n" to close it."""
    answers: list[str] = []
    for index, fund in enumerate(funds):
        if index:
            answers.append("y")
        answers.extend(fund)
    answers.append("n")
    return answers


def _said_once(prompter: "ScriptedPrompter", sentence: str) -> bool:
    """Whether `sentence` was printed exactly once.

    Counted against the whole output with its whitespace collapsed, because
    `say_wrapped` reflows a paragraph to the depth it is said at -- a check
    line by line would be a check on where the wrap happened to fall.
    """
    return " ".join(" ".join(prompter.said).split()).count(sentence) == 1


def keep_fund_responses(value: str = "") -> list[str]:
    """Keep one saved fund, or change only its value: the "Keep this fund?"
    gate and then name, kind and value all left at their saved defaults."""
    return ["", "", "", value]


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

    def test_a_first_run_offers_the_5_25_rule_as_the_default(self):
        """With nothing saved, Enter accepts absolute 5 and relative 25."""
        assert self._asked(prompt_rebalance_band).endswith("[5]: ")
        assert self._asked(prompt_relative_rebalance_band).endswith("[25]: ")
        p = ScriptedPrompter(["", ""])
        assert prompt_rebalance_band(p) == Decimal(5)
        assert prompt_relative_rebalance_band(p) == Decimal(25)

    def test_the_default_can_be_typed_over(self):
        p = ScriptedPrompter(["3", "20"])
        assert prompt_rebalance_band(p) == Decimal(3)
        assert prompt_relative_rebalance_band(p) == Decimal(20)

    def test_a_saved_answer_is_still_offered_as_an_editable_default(self):
        """A saved answer takes the place of the 5/25 default: a returning
        user presses Enter to keep what they chose last time."""
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


class TestFundAllocationPrompt:
    def _asked(self, answers):
        asked = []
        responses = iter(answers)
        p = Prompter(
            input_func=lambda text: asked.append(text) or next(responses),
            print_func=lambda _: None,
        )
        return _prompt_fund_allocation(p), asked

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

    #: Kept, its one saved fund walked through unchanged, no fund added, and
    #: its cash left alone.
    KEEP_ACCOUNT = ["y", *keep_fund_responses(), "n", ""]

    def _run(self, names):
        p = ScriptedPrompter([*(self.KEEP_ACCOUNT * len(names)), "n"])
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
        p = ScriptedPrompter([*(self.KEEP_ACCOUNT * 2), "n"])
        accounts = prompt_accounts(p, self._saved(["Alpha", "Beta"]))
        assert [a.name for a in accounts] == ["Alpha", "Beta"]


class TestPromptAccounts:
    def test_add_one_new_account_with_three_funds(self):
        responses = [
            "y",  # Add an account?
            "1",  # account type -> Roth IRA
            "My Roth",  # nickname
            *fund_list_responses(
                fund_responses("VTI", US_STOCK_KIND, "6000"),
                fund_responses("VXUS", INTERNATIONAL_KIND, "2000"),
                fund_responses("BND", BOND_KIND, "2000"),
            ),
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
        assert [h.name for h in account.funds()] == ["VTI", "VXUS", "BND"]

    def test_a_multi_asset_fund_may_be_added_beside_single_asset_ones(self):
        """The point of the change: a 401(k) holding a 2050 fund and an index
        fund is an ordinary lineup, and used to be unenterable."""
        responses = [
            "y", "4", "401k",  # Traditional 401(k)
            *fund_list_responses(
                fund_responses("VIIIX", US_STOCK_KIND, "20000"),
                multi_asset_fund_responses("Target 2050", "30000"),
            ),
            "0",
            "n",
        ]
        p = ScriptedPrompter(responses)
        account = prompt_accounts(p, [])[0]
        assert p.all_consumed()
        assert [h.fund_type for h in account.funds()] == [
            FundType.US_STOCK,
            FundType.MULTI_ASSET,
        ]
        assert account.funds()[1].allocation.bond_pct == Decimal(20)

    def test_two_funds_of_one_asset_class_may_be_declared(self):
        """A brokerage holding both VTI and VOO is two positions of one asset
        class, which the model now has no reason to refuse."""
        responses = [
            "y", "10", "Brokerage",
            *fund_list_responses(
                fund_responses("VTI", US_STOCK_KIND, "60000"),
                fund_responses("VOO", US_STOCK_KIND, "10000"),
            ),
            "0",
            "n",
        ]
        p = ScriptedPrompter(responses)
        account = prompt_accounts(p, [])[0]
        assert p.all_consumed()
        assert [h.name for h in account.funds()] == ["VTI", "VOO"]

    def test_duplicate_nickname_is_rejected_and_retried(self):
        responses = [
            "y", "1", "First",
            *fund_list_responses(fund_responses("VTI", US_STOCK_KIND, "0")), "0",
            "y", "1", "First", "SecondUnique",
            *fund_list_responses(known_fund_responses("VTI", "0")), "0",
            "n",
        ]
        p = ScriptedPrompter(responses)
        accounts = prompt_accounts(p, [])
        assert [a.name for a in accounts] == ["First", "SecondUnique"]

    def test_multi_asset_sleeves_over_100_are_rejected_and_retried(self):
        """Only two sleeves are asked for -- the third is what they leave --
        so the one way to be wrong is for the two to exceed 100 outright."""
        responses = [
            "y", "1", "401k",
            "Target 2050", MULTI_ASSET_KIND,
            "50", "60",  # 110 between them, leaving less than no bonds
            "60", "20", "y",  # valid, and the derived 20% bonds confirmed
            "10000",  # value
            "n",  # no more funds
            "0",  # cash
            "n",  # Add another account?
        ]
        p = ScriptedPrompter(responses)
        accounts = prompt_accounts(p, [])
        assert p.all_consumed()
        allocation = accounts[0].funds()[0].allocation
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
            *keep_fund_responses(),  # VTI unchanged, name, kind and value alike
            "n",  # no funds to add
            "",  # cash default (0)
            "n",  # Add another account?
        ]
        p = ScriptedPrompter(responses)
        accounts = prompt_accounts(p, [existing])
        assert p.all_consumed()
        assert len(accounts) == 1
        assert accounts[0].funds()[0].value == Decimal(6000)

    def test_a_saved_ticker_is_offered_as_an_editable_default(self):
        """A fund's name is the part most likely to change -- a plan swaps
        its bond fund, or a slot opened for a fund not yet bought is filled
        with a different one -- so it is re-asked with the old one
        pre-filled."""
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
            *keep_fund_responses(),  # U.S. stock fund unchanged
            *keep_fund_responses(),  # international unchanged
            "", "VBTLX", "", "",  # kept, renamed, same kind, same value
            "n",  # no funds to add
            "",  # cash
            "n",
        ]
        p = ScriptedPrompter(responses)
        accounts = prompt_accounts(p, [existing])
        assert p.all_consumed()
        bond = accounts[0].funds()[2]
        assert bond.name == "VBTLX"
        assert bond.value == Decimal(0)

    def test_a_saved_fund_can_be_dropped(self):
        """The fund list is one the user builds, so it has to shrink as well
        as grow -- and "Keep this fund?" is the same gate, default and
        message an account is removed through."""
        existing = Account(
            account_type="Roth IRA",
            name="My Roth",
            tax_treatment=TaxTreatment.TAX_FREE,
            holdings=[
                Holding(fund_type=FundType.US_STOCK, name="VTI", value=Decimal(6000)),
                Holding(fund_type=FundType.US_BOND, name="BND", value=Decimal(0)),
            ],
        )
        responses = [
            "y",  # Keep account 'My Roth'?
            *keep_fund_responses(),  # keep VTI
            "n",  # Keep BND? -> no
            "n",  # no funds to add
            "",  # cash
            "n",
        ]
        p = ScriptedPrompter(responses)
        accounts = prompt_accounts(p, [existing])
        assert p.all_consumed()
        assert [h.name for h in accounts[0].funds()] == ["VTI"]
        assert any("Removed 'BND'." in line for line in p.said)

    def test_a_saved_account_can_gain_a_fund(self):
        existing = Account(
            account_type="Roth IRA",
            name="My Roth",
            tax_treatment=TaxTreatment.TAX_FREE,
            holdings=[Holding(fund_type=FundType.US_STOCK, name="VTI", value=Decimal(6000))],
        )
        responses = [
            "y",  # Keep account 'My Roth'?
            *keep_fund_responses(),  # keep VTI
            "y",  # Add another fund?
            *fund_responses("BND", BOND_KIND, "0"),
            "n",  # no more
            "",  # cash
            "n",
        ]
        p = ScriptedPrompter(responses)
        accounts = prompt_accounts(p, [existing])
        assert p.all_consumed()
        assert [h.name for h in accounts[0].funds()] == ["VTI", "BND"]

    def test_a_saved_fund_can_change_kind_and_is_then_asked_for_its_mix(self):
        """Changing a fund's kind is one keystroke rather than a removal and
        a re-entry -- and a fund that has just become multi-asset has no
        saved mix, so it is asked for outright."""
        existing = Account(
            account_type="Roth IRA",
            name="My Roth",
            tax_treatment=TaxTreatment.TAX_FREE,
            holdings=[Holding(fund_type=FundType.US_STOCK, name="VSMGX", value=Decimal(6000))],
        )
        responses = [
            "y",  # Keep account 'My Roth'?
            "",  # Keep VSMGX?
            "",  # name unchanged
            MULTI_ASSET_KIND,  # ...but it is a LifeStrategy fund, not an index one
            "36", "24", "y",  # 60/40, stocks split; 40% bonds derived
            "",  # value unchanged
            "n",  # no funds to add
            "",  # cash
            "n",
        ]
        p = ScriptedPrompter(responses)
        accounts = prompt_accounts(p, [existing])
        assert p.all_consumed()
        fund = accounts[0].funds()[0]
        assert fund.fund_type == FundType.MULTI_ASSET
        assert fund.allocation.bond_pct == Decimal(40)
        assert fund.value == Decimal(6000)

    def test_a_saved_account_holding_nothing_is_asked_as_though_it_were_new(self):
        """An account with no saved funds has nothing to pre-fill, so it gets
        the same questions -- and the same explanation -- a new one does."""
        existing = Account(
            account_type="Roth IRA",
            name="My Roth",
            tax_treatment=TaxTreatment.TAX_DEFERRED,
            holdings=[],
        )
        responses = [
            "y",  # Keep account 'My Roth'?
            *fund_list_responses(fund_responses("VTI", US_STOCK_KIND, "1000")),
            "0",  # cash
            "n",  # Add another account?
        ]
        p = ScriptedPrompter(responses)
        accounts = prompt_accounts(p, [existing])
        assert p.all_consumed()
        assert accounts[0].name == "My Roth"
        assert accounts[0].funds()[0].name == "VTI"
        assert _said_once(p, FUND_EXPLANATION)

    def test_a_duplicate_fund_name_is_rejected_and_retried(self):
        """An account may not hold two funds under one name -- that name is
        what an order is placed against. With the fund list free-form that is
        something a user can type, so it is a re-ask here rather than a
        ValueError out of `Account`."""
        responses = [
            "y", "1", "My Roth",
            *fund_responses("VTI", US_STOCK_KIND, "100"),
            "y",
            "VTI",  # the same ticker again
            *fund_responses("VXUS", INTERNATIONAL_KIND, "50"),
            "n",
            "0",
            "n",
        ]
        p = ScriptedPrompter(responses)
        accounts = prompt_accounts(p, [])
        assert p.all_consumed()
        assert [h.name for h in accounts[0].funds()] == ["VTI", "VXUS"]
        assert any("'VTI' is already in this account" in line for line in p.said)

    def test_the_clash_ignores_case_and_surrounding_space(self):
        """The same comparison `Account` makes, so the prompt cannot accept
        something the model will then refuse."""
        responses = [
            "y", "1", "My Roth",
            *fund_responses("VTI", US_STOCK_KIND, "100"),
            "y",
            " vti ",
            *fund_responses("BND", BOND_KIND, "0"),
            "n",
            "0",
            "n",
        ]
        p = ScriptedPrompter(responses)
        accounts = prompt_accounts(p, [])
        assert p.all_consumed()
        assert [h.name for h in accounts[0].funds()] == ["VTI", "BND"]

    def test_a_saved_fund_keeping_its_own_name_is_not_a_clash(self):
        """Pressing Enter through a saved ticker has to keep it -- the fund
        being re-asked is not yet among the names collected."""
        existing = Account(
            account_type="Roth IRA",
            name="My Roth",
            tax_treatment=TaxTreatment.TAX_FREE,
            holdings=[
                Holding(fund_type=FundType.US_STOCK, name="VTI", value=Decimal(100)),
                Holding(fund_type=FundType.US_BOND, name="BND", value=Decimal(0)),
            ],
        )
        p = ScriptedPrompter([
            "y",
            *keep_fund_responses(),
            *keep_fund_responses(),
            "n", "", "n",
        ])
        accounts = prompt_accounts(p, [existing])
        assert p.all_consumed()
        assert [h.name for h in accounts[0].funds()] == ["VTI", "BND"]

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
            *fund_list_responses(fund_responses("VTI", US_STOCK_KIND, "0")),
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

    def test_updating_a_multi_asset_fund_offers_its_saved_mix(self):
        existing = Account(
            account_type="Roth 401(k)",
            name="401k",
            tax_treatment=TaxTreatment.TAX_DEFERRED,
            holdings=[
                Holding(
                    fund_type=FundType.MULTI_ASSET,
                    name="Target 2050",
                    value=Decimal(3000),
                    allocation=FundAllocation(
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
            "",  # Keep 'Target 2050'?
            "",  # name unchanged
            "",  # kind unchanged -> still a mix
            "y",  # update the fund's underlying allocation?
            "70", "15", "y",  # new underlying allocation; 15% bonds derived
            "",  # value unchanged
            "n",  # no funds to add
            "200",  # cash -> update to 200
            "n",  # Add another account?
        ]
        p = ScriptedPrompter(responses)
        accounts = prompt_accounts(p, [existing])
        assert p.all_consumed()
        updated = accounts[0]
        assert updated.funds()[0].allocation.us_stock_pct == Decimal(70)
        assert updated.available_cash() == Decimal(200)
        # The mix being replaced is shown, so the answer isn't from memory. It
        # is the *old* one: the question hasn't been answered yet at that point.
        assert any(
            "Currently 60% U.S. stocks, 20% international stocks, and 20% bonds" in line
            for line in p.said
        )

    def test_a_fund_with_no_position_is_still_declared(self):
        """A fund the user owns none of becomes a slot the solver can buy
        into. Leaving it out is what removes the only place an asset class
        could go."""
        responses = [
            "y", "10",  # account type "Brokerage" is index 10 in ACCOUNT_TYPE_CHOICES
            "Brokerage",
            *fund_list_responses(
                fund_responses("VTI", US_STOCK_KIND, "60000"),
                fund_responses("VXUS", INTERNATIONAL_KIND, "30000"),
                fund_responses("BND", BOND_KIND, ""),  # named, nothing held yet
            ),
            "0",  # cash
            "n",
        ]
        p = ScriptedPrompter(responses)
        accounts = prompt_accounts(p, [])
        assert p.all_consumed()
        bond_slot = accounts[0].funds()[2]
        assert bond_slot.name == "BND"
        assert bond_slot.value == Decimal(0)

    def test_the_fund_questions_are_introduced_once(self):
        """Nothing in "Add another fund?" says which funds belong in the
        answer, so the one sentence above the list says it."""
        responses = [
            "y", "1", "My Roth",
            *fund_list_responses(
                fund_responses("VTI", US_STOCK_KIND, "1"),
                fund_responses("VXUS", INTERNATIONAL_KIND, "0"),
                fund_responses("BND", BOND_KIND, "0"),
            ),
            "0",
            "n",
        ]
        p = ScriptedPrompter(responses)
        prompt_accounts(p, [])
        assert _said_once(p, FUND_EXPLANATION)

    def test_the_fund_kinds_are_listed_once_per_account(self):
        """Four choices reprinted under every fund is most of the screen, so
        later funds ask in one line -- the same treatment the eleven account
        types get."""
        responses = [
            "y", "1", "My Roth",
            *fund_list_responses(
                fund_responses("VTI", US_STOCK_KIND, "1"),
                fund_responses("VXUS", INTERNATIONAL_KIND, "0"),
            ),
            "0",
            "n",
        ]
        p = ScriptedPrompter(responses)
        prompt_accounts(p, [])
        assert sum("1. U.S. stocks" in text for text in p.said) == 1

    def test_the_asset_location_note_is_not_said_during_onboarding(self):
        """It explained a trade the user had not seen yet. The README's
        "Asset location" entry is where it lives now."""
        responses = [
            "y", "10", "Brokerage",
            *fund_list_responses(fund_responses("BND", BOND_KIND, "1000")),
            "0",
            "n",
        ]
        p = ScriptedPrompter(responses)
        prompt_accounts(p, [])
        assert not any("taxed yearly as ordinary income" in text for text in p.said)


class TestFundsAreRememberedByName:
    """A fund name maps to one set of details across every account. A name
    already known is shown and confirmed rather than described again, and a
    change to it reaches every account holding it."""

    def test_a_fund_typed_into_a_second_account_is_confirmed_not_re_asked(self):
        responses = [
            "y", "1", "Roth",
            *fund_list_responses(multi_asset_fund_responses("VBIAX", "100", "60", "0")), "0",
            "y", "10", "Brokerage",
            *fund_list_responses(known_fund_responses("vbiax", "200")), "0",
            "n",
        ]
        p = ScriptedPrompter(responses)
        accounts = prompt_accounts(p, [])
        assert p.all_consumed()
        holding = accounts[1].funds()[0]
        assert (holding.name, holding.value) == ("vbiax", Decimal(200))
        assert holding.allocation.bond_pct == Decimal(40)
        assert _said_once(
            p,
            "Saved details: VBIAX is a multi-asset fund that holds 60% U.S. stocks, "
            "0% international stocks, and 40% bonds.",
        )

    def test_changing_a_known_fund_changes_it_in_every_account(self):
        responses = [
            "y", "1", "Roth",
            *fund_list_responses(fund_responses("VTI", US_STOCK_KIND, "100")), "0",
            "y", "10", "Brokerage",
            # Decline the saved details, and call it a bond fund instead.
            *fund_list_responses(["vti", "n", BOND_KIND, "200"]), "0",
            "n",
        ]
        p = ScriptedPrompter(responses)
        catalog = FundCatalog()
        accounts = prompt_accounts(p, [], catalog)
        assert p.all_consumed()
        # A lookup in another spelling is not a rename of the fund.
        assert [f.name for f in catalog.profiles()] == ["VTI"]
        assert [a.funds()[0].name for a in accounts] == ["VTI", "vti"]
        assert [a.funds()[0].fund_type for a in accounts] == [FundType.US_BOND] * 2
        assert [a.funds()[0].value for a in accounts] == [Decimal(100), Decimal(200)]
        assert _said_once(p, "This changes VTI's details in every account that holds it.")

    def test_a_saved_fund_offers_details_changed_earlier_in_the_run(self):
        """The first account re-describes VTI; the second, walked after it,
        offers the new kind as its default rather than its own old copy."""
        saved = [
            Account(
                account_type="Brokerage",
                name=name,
                tax_treatment=TaxTreatment.TAXABLE,
                holdings=[Holding(fund_type=FundType.US_STOCK, name="VTI", value=Decimal(1))],
            )
            for name in ("First", "Second")
        ]
        responses = [
            "",  # Keep this account?
            "", "", INTERNATIONAL_KIND, "",  # keep VTI, but it holds international stocks
            "n", "",
            "",  # Keep this account?
            *keep_fund_responses(),
            "n", "",
            "n",  # Add another account?
        ]
        p = ScriptedPrompter(responses)
        accounts = prompt_accounts(p, saved)
        assert p.all_consumed()
        assert [a.funds()[0].fund_type for a in accounts] == [FundType.INTERNATIONAL_STOCK] * 2

    def test_a_fund_moved_between_accounts_in_one_run_is_still_recognized(self):
        """Removed from one account and added to another in the same session:
        the catalog forgets a fund only when the run is saved."""
        saved = [
            Account(
                account_type="Brokerage",
                name="Old",
                tax_treatment=TaxTreatment.TAXABLE,
                holdings=[Holding(fund_type=FundType.US_BOND, name="BND", value=Decimal(1))],
            )
        ]
        responses = [
            "",  # Keep this account?
            "n",  # Keep BND? -- no
            *fund_responses("VTI", US_STOCK_KIND, "1"),
            "n", "",
            "y", "10", "New",  # Add another account?
            *fund_list_responses(known_fund_responses("BND", "50")), "0",
            "n",
        ]
        p = ScriptedPrompter(responses)
        accounts = prompt_accounts(p, saved)
        assert p.all_consumed()
        assert accounts[1].funds()[0].fund_type == FundType.US_BOND

    def test_confirming_saved_details_records_nothing_new(self):
        catalog = FundCatalog([FundProfile(name="BND", fund_type=FundType.US_BOND)])
        p = ScriptedPrompter([
            "y", "10", "Brokerage",
            *fund_list_responses(known_fund_responses("BND", "50")), "0",
            "n",
        ])
        prompt_accounts(p, [], catalog)
        assert "This changes" not in p.text
        assert [f.name for f in catalog.profiles()] == ["BND"]

    def test_saved_details_name_a_single_asset_fund_by_its_asset_class(self):
        catalog = FundCatalog([
            FundProfile(name="VTI", fund_type=FundType.US_STOCK),
            FundProfile(name="VXUS", fund_type=FundType.INTERNATIONAL_STOCK),
        ])
        p = ScriptedPrompter([
            "y", "10", "Brokerage",
            *fund_list_responses(
                known_fund_responses("VTI", "1"), known_fund_responses("VXUS", "1")
            ),
            "0",
            "n",
        ])
        prompt_accounts(p, [], catalog)
        assert p.all_consumed()
        assert _said_once(p, "Saved details: VTI is a U.S. stock fund.")
        assert _said_once(p, "Saved details: VXUS is an international stock fund.")
