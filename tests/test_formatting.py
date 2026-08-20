from three_fund_rebalance.formatting import (
    format_account_heading,
    format_section_header,
    format_subheading,
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
        heading = format_account_heading("Fidelity Roth", "Roth IRA")
        assert heading == "Fidelity Roth (Roth IRA)"
        assert "\n" not in heading

    def test_carries_no_rule_of_its_own_so_depth_alone_places_it(self):
        """The level below a subheading is shown by indentation, not by a
        third rule style competing with the two above it."""
        heading = format_account_heading("Fidelity Roth", "Roth IRA")
        assert not any(rule_char in heading for rule_char in "=-~.")
        assert format_subheading("Saved accounts").split("\n")[1][0] == "-"
        assert format_section_header(1, 3, "Account holdings").split("\n")[0][0] == "="
