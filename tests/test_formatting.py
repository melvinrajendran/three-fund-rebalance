from decimal import Decimal

from three_fund_rebalance.formatting import (
    describe_as_of,
    format_account_heading,
    format_date,
    format_percent_prose,
    format_percents,
    format_result_header,
    format_section_header,
    format_subheading,
    percent_places,
)


class TestSectionHeader:
    def test_banner_names_the_step_and_rules_to_a_fixed_width(self):
        rule, label, closing_rule = format_section_header(2, 3, "Account holdings").split("\n")
        assert label == "STEP 2 OF 3: ACCOUNT HOLDINGS"
        assert set(rule) == {"="}
        assert rule == closing_rule

    def test_every_banner_shares_one_width_regardless_of_title_length(self):
        widths = {
            len(format_section_header(1, 3, title).split("\n")[0])
            for title in ("Target asset allocation", "Account holdings", "Rebalancing trades")
        }
        assert len(widths) == 1

    def test_a_title_longer_than_the_default_widens_the_rule_to_match(self):
        rule, label, _ = format_section_header(1, 1, "x" * 80).split("\n")
        assert len(rule) == len(label)


class TestSubheading:
    def test_underlines_to_the_width_of_its_own_text(self):
        text, rule = format_subheading("Stock/bond split").split("\n")
        assert text == "Stock/bond split"
        assert rule == "-" * len("Stock/bond split")

    def test_stays_narrower_than_a_section_banner_so_the_levels_read_apart(self):
        subheading_rule = format_subheading("Recommended trades").split("\n")[1]
        banner_rule = format_section_header(3, 3, "Rebalancing trades").split("\n")[0]
        assert set(subheading_rule) == {"-"}
        assert len(subheading_rule) < len(banner_rule)


class TestAccountHeading:
    def test_names_the_account_and_its_type_on_one_plain_line(self):
        heading = format_account_heading("Vanguard Roth", "Roth IRA")
        assert heading == "Vanguard Roth (Roth IRA)"
        assert "\n" not in heading

    def test_carries_no_rule_of_its_own_so_depth_alone_places_it(self):
        """The level below a subheading is shown by indentation, not by a
        third rule style competing with the two above it."""
        heading = format_account_heading("Vanguard Roth", "Roth IRA")
        assert not any(rule_char in heading for rule_char in "=-~.")
        assert format_subheading("Saved accounts").split("\n")[1][0] == "-"
        assert format_section_header(1, 3, "Account holdings").split("\n")[0][0] == "="


class TestResultHeader:
    def test_banner_carries_no_step_number(self):
        """The report is what the steps produced, not another one of them."""
        rule, label, closing_rule = format_result_header("Your rebalancing plan").split("\n")
        assert label == "YOUR REBALANCING PLAN"
        assert "STEP" not in label
        assert set(rule) == {"="}
        assert rule == closing_rule

    def test_ruled_to_the_same_width_as_a_step_banner(self):
        """Both sit at the top level, so they have to line up as the run
        scrolls past."""
        result_rule = format_result_header("Your rebalancing plan").split("\n")[0]
        step_rule = format_section_header(1, 3, "Target asset allocation").split("\n")[0]
        assert len(result_rule) == len(step_rule)


class TestPercentPrecision:
    def test_a_single_percentage_is_written_as_short_as_it_goes(self):
        assert format_percent_prose(Decimal(20)) == "20"
        assert format_percent_prose(Decimal("20.0")) == "20"
        assert format_percent_prose(Decimal("62.5")) == "62.5"

    def test_a_column_is_written_at_the_precision_its_widest_value_needs(self):
        """The figures are read down the page, so they line up on the decimal
        point rather than each being as short as it could be alone."""
        assert format_percents([Decimal("62.5"), Decimal(38), Decimal(0)]) == [
            "62.5",
            "38.0",
            "0.0",
        ]

    def test_a_column_of_whole_numbers_carries_no_decimal_point(self):
        assert format_percents([Decimal(50), Decimal("30.0"), Decimal(20)]) == ["50", "30", "20"]

    def test_a_drift_keeps_its_sign_so_the_direction_reads(self):
        assert format_percents([Decimal("23.7"), Decimal(-10)], signed=True) == ["+23.7", "-10.0"]

    def test_the_figure_behind_a_percentage_is_rounded_before_it_is_measured(self):
        """These come out of non-terminating divisions, so the precision is
        decided on what will be printed rather than on what was computed."""
        third = Decimal(20_000) / Decimal(3) / Decimal(1000) * Decimal(100)  # 666.66...
        assert percent_places([Decimal(20) - Decimal("0.0000001")]) == 0
        assert format_percent_prose(third) == "666.7"

    def test_half_even_is_kept_so_a_band_edge_prints_as_it_always_has(self):
        assert format_percent_prose(Decimal("6.25")) == "6.2"


class TestDates:
    def test_every_spelling_of_a_date_comes_out_written_in_full(self):
        assert format_date("2026-07-31") == "July 31, 2026"
        assert format_date("2026-07-31T00:00:00") == "July 31, 2026"
        assert format_date("July 31, 2026") == "July 31, 2026"

    def test_a_note_in_place_of_a_date_is_passed_through(self):
        assert format_date("manually entered") == "manually entered"
        assert format_date("") == "unknown date"

    def test_as_of_is_said_only_where_there_is_a_date_to_say_it_of(self):
        """"as of manually entered" is not a sentence."""
        assert describe_as_of("2026-07-31") == "as of July 31, 2026"
        assert describe_as_of("manually entered") == "manually entered"
