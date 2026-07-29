"""Unit tests for :func:`make_daily.extract_meter_from_spec`.

This is a pure function (no DB dependency), so it can be tested directly.
"""
import pandas as pd
from make_daily import extract_meter_from_spec


class TestExtractMeterFromSpec:
    """Cover all branches of the meter-extraction logic."""

    # ---------- NaN / None ----------
    def test_na_returns_zero(self):
        assert extract_meter_from_spec(pd.NA) == 0

    def test_none_returns_zero(self):
        assert extract_meter_from_spec(None) == 0

    # ---------- Chinese number mapping ----------
    def test_half_meter_chinese(self):
        assert extract_meter_from_spec("花型,半米") == 0.5

    def test_half_meter_short(self):
        assert extract_meter_from_spec("花型,半") == 0.5

    def test_one_meter_chinese(self):
        assert extract_meter_from_spec("花型,一米") == 1

    def test_two_meters_chinese_yi(self):
        assert extract_meter_from_spec("花型,二米") == 2

    def test_two_meters_chinese_liang(self):
        assert extract_meter_from_spec("花型,两米") == 2

    def test_three_meters_chinese(self):
        assert extract_meter_from_spec("花型,三米") == 3

    def test_five_meters_chinese(self):
        assert extract_meter_from_spec("花型,五米") == 5

    # ---------- Digit + unit ----------
    def test_digit_meter(self):
        assert extract_meter_from_spec("花型,2米") == 2

    def test_decimal_meter(self):
        assert extract_meter_from_spec("花型,2.5米") == 2.5

    def test_digit_meter_lowercase_m(self):
        assert extract_meter_from_spec("花型,3m") == 3

    def test_decimal_meter_uppercase_m(self):
        assert extract_meter_from_spec("花型,1.5M") == 1.5

    # ---------- No comma in spec ----------
    def test_no_comma_no_number_returns_zero(self):
        """If there is no comma and no number pattern → 0."""
        assert extract_meter_from_spec("花型") == 0

    def test_no_comma_but_has_number(self):
        """If there is no comma but the whole string has a number pattern."""
        assert extract_meter_from_spec("花型2米") == 2

    # ---------- Width in second part ----------
    def test_width_in_second_part(self):
        """Width (e.g. 1.5米 wide) in the comma-separated part is the meter."""
        assert extract_meter_from_spec("花型,1.5米") == 1.5

    def test_number_without_unit(self):
        """Number without 米/m unit → 0."""
        assert extract_meter_from_spec("花型,2") == 0

    # ---------- Brackets in second part ----------
    def test_bracket_in_second_part(self):
        """Brackets in the second part are stripped."""
        assert extract_meter_from_spec("花型,2米（促销）") == 2

    def test_chinese_bracket_in_second_part(self):
        """Chinese brackets in second part are stripped."""
        assert extract_meter_from_spec("花型，2．5米（特惠）") == 2.5

    # ---------- Whitespace handling ----------
    def test_whitespace_around_number(self):
        assert extract_meter_from_spec("花型, 2.5 米") == 2.5

    def test_whitespace_only_spec(self):
        assert extract_meter_from_spec("   ") == 0
