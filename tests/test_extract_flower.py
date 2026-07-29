"""Unit tests for :func:`make_daily.extract_flower_from_spec`.

This is a pure function (no DB dependency), so it can be tested directly.
"""
import pandas as pd
from make_daily import extract_flower_from_spec


class TestExtractFlowerFromSpec:
    """Cover all branches of the flower-extraction logic."""

    def test_spec_with_english_comma(self):
        """English comma → return first part."""
        assert extract_flower_from_spec("花型A,红色") == "花型A"

    def test_spec_with_chinese_comma(self):
        """Chinese comma → return first part."""
        assert extract_flower_from_spec("花型A，红色") == "花型A"

    def test_spec_with_chinese_parentheses(self):
        """Chinese brackets → return text before them."""
        assert extract_flower_from_spec("花型A（红色）") == "花型A"

    def test_spec_with_english_parentheses(self):
        """English parentheses → return text before them."""
        assert extract_flower_from_spec("花型A(红色)") == "花型A"

    def test_spec_no_separator(self):
        """Plain string with no delimiter → return as-is."""
        assert extract_flower_from_spec("花型A") == "花型A"

    def test_spec_na_value(self):
        """pd.NA → None."""
        assert extract_flower_from_spec(pd.NA) is None

    def test_spec_none(self):
        """Python None → None."""
        assert extract_flower_from_spec(None) is None

    def test_spec_empty_string(self):
        """Empty string → None."""
        assert extract_flower_from_spec("") is None

    def test_spec_whitespace_only(self):
        """Whitespace-only string → None."""
        assert extract_flower_from_spec("   ") is None

    def test_spec_trailing_whitespace(self):
        """String with leading/trailing whitespace → stripped result."""
        assert extract_flower_from_spec("  花型A,红色  ") == "花型A"

    def test_flower_after_comma_is_empty(self):
        """Empty content after comma → return first part."""
        assert extract_flower_from_spec("花型A,") == "花型A"

    def test_flower_before_bracket_is_empty(self):
        """Empty content before bracket → return None."""
        assert extract_flower_from_spec("（红色）") is None

    def test_mixed_comma_and_bracket(self):
        """Comma takes priority over brackets."""
        assert extract_flower_from_spec("花型A,红色（特惠）") == "花型A"

    def test_parentheses_before_comma(self):
        """Brackets are checked only when there is no comma."""
        result = extract_flower_from_spec("花型A(红色)")
        assert result == "花型A"

    def test_chinese_mixed_punctuation(self):
        """Handle mixed Chinese/English punctuation."""
        assert extract_flower_from_spec("花型A，红色(促销)") == "花型A"
