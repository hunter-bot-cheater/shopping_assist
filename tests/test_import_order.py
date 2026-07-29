"""Tests for :mod:`import_order`.

Covers data-cleaning helpers (pure functions) and the main import pipeline
(mocked DB).
"""
from unittest.mock import MagicMock, patch, call
import pandas as pd
import pytest

from import_order import (
    clean_text,
    clean_datetime,
    clean_numeric,
    get_latest_file,
    import_excel_from_dataframe,
)


# ===========================================================================
# clean_text
# ===========================================================================

class TestCleanText:

    def test_normal_string(self):
        assert clean_text("  Hello  ") == "Hello"

    def test_na_value(self):
        assert clean_text(pd.NA) is None

    def test_nan_value(self):
        assert clean_text(float('nan')) is None

    def test_none_value(self):
        assert clean_text(None) is None

    def test_empty_string(self):
        assert clean_text("") is None

    def test_nan_string(self):
        assert clean_text("nan") is None

    def test_none_string(self):
        assert clean_text("None") is None

    def test_tabs_stripped(self):
        assert clean_text("\t商品\t") == "商品"


# ===========================================================================
# clean_datetime
# ===========================================================================

class TestCleanDatetime:

    def test_valid_datetime(self):
        result = clean_datetime("2026-07-15 10:30:00")
        assert result is not None
        assert str(result)[:10] == "2026-07-15"

    def test_na_value(self):
        assert clean_datetime(pd.NA) is None

    def test_empty_string(self):
        assert clean_datetime("") is None

    def test_invalid_string(self):
        result = clean_datetime("not-a-date")
        assert result is None

    def test_nan_string(self):
        assert clean_datetime("nan") is None


# ===========================================================================
# clean_numeric
# ===========================================================================

class TestCleanNumeric:

    def test_integer_string(self):
        assert clean_numeric("123") == 123.0

    def test_decimal_string(self):
        assert clean_numeric("45.67") == 45.67

    def test_na_value(self):
        assert clean_numeric(pd.NA) is None

    def test_invalid_string(self):
        assert clean_numeric("abc") is None

    def test_none_value(self):
        assert clean_numeric(None) is None


# ===========================================================================
# get_latest_file
# ===========================================================================

class TestGetLatestFile:

    @patch('os.path.getmtime')
    @patch('glob.glob')
    def test_finds_latest(self, mock_glob, mock_mtime):
        """Should return the file with the latest modification time."""
        mock_glob.return_value = ['/d/f1.xlsx', '/d/f2.xlsx']
        mock_mtime.side_effect = [100, 200]  # f2 is newer
        result = get_latest_file('/d', '*.xlsx')
        assert result == '/d/f2.xlsx'

    @patch('glob.glob')
    def test_no_files(self, mock_glob):
        mock_glob.return_value = []
        assert get_latest_file('/d', '*.xlsx') is None


# ===========================================================================
# import_excel_from_dataframe
# ===========================================================================

class TestImportExcelFromDataFrame:

    def test_missing_required_columns(self):
        """Should reject DataFrames lacking '订单号'."""
        df = pd.DataFrame({'商品': ['A'], '商家实收金额(元)': [10.0]})  # missing 订单号
        result = import_excel_from_dataframe(df)
        assert result['success'] is False
        assert '缺少必要列' in result['message']

    def test_empty_dataframe(self):
        """Empty DataFrame should be rejected early."""
        df = pd.DataFrame({'订单号': [], '商品': [], '商家实收金额(元)': []})
        result = import_excel_from_dataframe(df)
        assert result['success'] is False
        assert '数据为空' in result['message']

    @patch('import_order.engine')
    def test_successful_import(self, mock_engine, mock_conn):
        """Happy-path import with valid data."""
        df = pd.DataFrame({
            '订单号': ['ORD001', 'ORD002'],
            '商品': ['花型A布料', '花型B布料'],
            '商家实收金额(元)': [50.0, 80.0],
            '商品规格': ['规格A', '规格B'],
        })
        # mock_engine.begin → mock connection
        mock_engine.begin.return_value.__enter__.return_value = mock_conn

        result = import_excel_from_dataframe(df, 'test.xlsx')
        assert result['success'] is True
        assert result['stats']['成功导入'] == 2
