"""Unit tests for :func:`extract_flower_from_spec` (shared by import_order / make_daily).

This is a pure function (no DB dependency), so it can be tested directly.
"""
import pandas as pd
from import_order import extract_flower_from_spec


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

    def test_taobao_sku_prefix(self):
        """淘宝 SKU 格式：颜色分类:XX一米（门幅...）"""
        assert extract_flower_from_spec("颜色分类:映日荷花一米（门幅1.43米）") == "映日荷花"

    def test_taobao_sku_length_self(self):
        """淘宝 SKU 格式：颜色分类:XX两米（长度自选）"""
        assert extract_flower_from_spec("颜色分类:黑底条纹两米（长度自选）") == "黑底条纹"

    def test_taobao_sku_multi_cut(self):
        """淘宝 SKU 格式：XX一米价格多拍连裁"""
        assert extract_flower_from_spec("颜色分类:蓝色唐人一米价格多拍连裁") == "蓝色唐人"

    def test_taobao_sku_no_bracket(self):
        """淘宝 SKU 无括号：颜色分类:XX三米"""
        assert extract_flower_from_spec("颜色分类:几何乱纹三米") == "几何乱纹"

    def test_taobao_sku_metric_unit(self):
        """淘宝 SKU 数字米数：颜色分类:XX 1米"""
        assert extract_flower_from_spec("颜色分类:腰果花 1米") == "腰果花"
