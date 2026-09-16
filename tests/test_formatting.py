from datetime import datetime, timedelta, tzinfo
from decimal import Decimal
from zoneinfo import ZoneInfo

from three_fund_rebalance.formatting import (
    describe_as_of,
    fixed_width,
    format_account_heading,
    format_and_list,
    format_date,
    format_fund_mix,
    format_generated_at,
    format_generated_at_for_filename,
    format_percent_prose,
    format_percents,
    format_result_header,
    format_saved_at,
    format_section_header,
    format_subheading,
    format_zone_label,
    percent_places,
    prose_width,
    table_width,
)
from three_fund_rebalance.models import FundAllocation


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


class TestGeneratedAt:
    """The instant a plan was made, rendered for a sentence and for a file
    name. Two spellings of one decision -- same clock, same precision, same
    zone -- so a summary found on disk can be matched to its own contents.

    Every case builds its own zone, so nothing here depends on the machine
    the suite runs on."""

    def moment(self, zone):
        return datetime(2026, 8, 29, 21, 3, 33, tzinfo=ZoneInfo(zone))

    def test_it_reads_as_the_local_afternoon_it_was(self):
        """Not "1:03 AM UTC" -- a stamp nobody can use without arithmetic is
        a worse answer than no stamp."""
        moment = self.moment("America/New_York")
        assert format_generated_at(moment) == "August 29, 2026 at 9:03 PM EDT"
        assert format_generated_at_for_filename(moment) == "2026-08-29-2103-edt"

    def test_a_zone_with_no_abbreviation_falls_to_its_offset(self):
        """`tzname()` answers "+0545" for these, which is not a word and must
        not be printed as one."""
        moment = self.moment("Asia/Kathmandu")
        assert format_generated_at(moment) == "August 29, 2026 at 9:03 PM UTC+05:45"
        assert format_generated_at_for_filename(moment) == "2026-08-29-2103-utc+0545"

    def test_a_zone_behind_utc_keeps_its_sign(self):
        """Marquesas is the awkward one that is real -- behind UTC, on a half
        hour, and with no abbreviation, so `tzname()` answers "-0930"."""
        moment = self.moment("Pacific/Marquesas")
        assert format_generated_at(moment) == "August 29, 2026 at 9:03 PM UTC-09:30"
        assert format_generated_at_for_filename(moment) == "2026-08-29-2103-utc-0930"

    def test_a_windows_style_phrase_is_not_printed_as_an_abbreviation(self):
        """Windows answers "Eastern Daylight Time" -- localized, so on a
        non-English machine it is not even ASCII. The same rule that catches
        "+0545" catches this, which is the reason the rule is a shape test
        rather than a list of known zones."""

        class Phrase(tzinfo):
            def utcoffset(self, dt): return timedelta(hours=-4)
            def tzname(self, dt): return "Eastern Daylight Time"
            def dst(self, dt): return timedelta(0)

        moment = datetime(2026, 8, 29, 21, 3, tzinfo=Phrase())
        assert format_generated_at(moment).endswith("UTC-04:00")
        assert format_generated_at_for_filename(moment) == "2026-08-29-2103-utc-0400"

    def test_the_file_name_survives_a_shell_and_windows(self):
        for zone in ("America/New_York", "Asia/Kathmandu", "Pacific/Marquesas", "UTC"):
            stamp = format_generated_at_for_filename(self.moment(zone))
            # A comma, a space or a colon would each break one of those.
            assert not set(stamp) & set(", :/\\"), stamp

    def test_both_spellings_name_the_same_instant_and_zone(self):
        """The property that matters -- not the literal strings above, which
        say what they look like, but that neither can drift from the other's
        clock, minute or zone."""
        for zone in (
                "America/New_York", "Asia/Kolkata", "Asia/Kathmandu",
                "Pacific/Marquesas", "UTC",
            ):
            moment = self.moment(zone)
            said = format_generated_at(moment)
            when, _, label = said.rpartition(" ")
            assert datetime.strptime(when, "%B %d, %Y at %I:%M %p") == moment.replace(
                second=0, microsecond=0, tzinfo=None
            )
            assert format_generated_at_for_filename(moment) == (
                f"{moment:%Y-%m-%d-%H%M}-{label.lower().replace(':', '')}"
            )


class TestSavedAt:
    """When the portfolio file was last written, read back off disk. Nothing
    here builds a local zone or asks the machine for one: a saved stamp has
    to read the same on every machine that opens the file."""

    def test_it_reads_back_as_the_sentence_that_saved_it(self):
        assert (
            format_saved_at("2026-08-29T21:03:00-04:00", "EDT")
            == "August 29, 2026 at 9:03 PM EDT"
        )

    def test_the_saved_label_is_what_names_the_zone(self):
        """The offset alone cannot tell EDT from AST, so the label decides --
        and it is the same one `format_generated_at` printed that session,
        which is what `format_zone_label` is for."""
        moment = datetime(2026, 8, 29, 21, 3, tzinfo=ZoneInfo("America/New_York"))
        assert format_zone_label(moment) == "EDT"
        assert format_saved_at(moment.isoformat(timespec="seconds"), "AST").endswith("AST")

    def test_a_stamp_with_no_label_falls_to_its_own_offset(self):
        assert (
            format_saved_at("2026-08-29T21:03:00+05:45")
            == "August 29, 2026 at 9:03 PM UTC+05:45"
        )

    def test_a_label_that_is_not_a_zone_is_not_printed(self):
        """The file is hand-editable, so the label goes through the same
        shape test every zone here does."""
        said = format_saved_at("2026-08-29T21:03:00-04:00", "Eastern Daylight Time")
        assert said == "August 29, 2026 at 9:03 PM UTC-04:00"

    def test_a_file_saved_before_the_stamp_had_a_clock_keeps_its_bare_date(self):
        """Midnight is not when it was saved, and a time the program does not
        know is not one to invent."""
        assert format_saved_at("2026-08-21") == "August 21, 2026"
        assert format_saved_at("") == "unknown date"


class TestFixedWidth:
    """Writing to a file pins the layout, because a file is read somewhere
    other than the terminal that produced it."""

    def test_it_pins_both_widths_and_restores_them(self, monkeypatch):
        """At 80 columns a file's prose is 78 and its tables keep their
        100-column floor."""
        monkeypatch.setenv("COLUMNS", "200")
        before = (prose_width(), table_width())
        with fixed_width(80):
            assert (prose_width(), table_width()) == (78, 100)
        assert (prose_width(), table_width()) == before

    def test_it_restores_the_previous_setting_when_nested(self):
        with fixed_width(140):
            with fixed_width(80):
                assert table_width() == 100
            assert table_width() == 138


class TestFormatAndList:
    """A set of names is joined as prose rather than as a bare comma list,
    because every line one appears in is a sentence."""

    def test_one_name_stands_alone(self):
        assert format_and_list(["A"]) == "A"

    def test_two_names_take_no_comma(self):
        """The serial comma separates three or more; "A, and B" reads as a
        stray one."""
        assert format_and_list(["A", "B"]) == "A and B"

    def test_three_or_more_take_the_serial_comma(self):
        assert format_and_list(["A", "B", "C"]) == "A, B, and C"
        assert format_and_list(["A", "B", "C", "D"]) == "A, B, C, and D"


class TestFormatFundMix:
    """A multi-asset fund's mix as a table, the same in the prompt that shows
    a saved fund and in the report that restates it. Here rather than in
    either because `report` may not import `prompts`."""

    def test_it_lists_the_three_classes_in_the_report_s_order(self):
        assert format_fund_mix(
            FundAllocation(
                us_stock_pct=Decimal(54),
                international_stock_pct=Decimal(36),
                bond_pct=Decimal(10),
            )
        ) == [
            "U.S. stocks           54%",
            "International stocks  36%",
            "Bonds                 10%",
        ]

    def test_a_zero_sleeve_is_still_listed(self):
        """All three, always: leaving one out leaves the reader to work out
        that the fund holds none of it."""
        lines = format_fund_mix(
            FundAllocation(
                us_stock_pct=Decimal(60),
                international_stock_pct=Decimal(0),
                bond_pct=Decimal(40),
            )
        )
        assert lines[1] == "International stocks   0%"

    def test_shares_are_read_back_as_entered_and_line_up_on_the_percent_sign(self):
        """The fund's own figures at their own precision -- not rounded by
        `format_percents`, and not the normalized fractions the solver uses --
        so they are right-aligned rather than aligned on a decimal point."""
        assert format_fund_mix(
            FundAllocation(
                us_stock_pct=Decimal("64.1"),
                international_stock_pct=Decimal("34.34"),
                bond_pct=Decimal("1.56"),
            )
        ) == [
            "U.S. stocks            64.1%",
            "International stocks  34.34%",
            "Bonds                  1.56%",
        ]
