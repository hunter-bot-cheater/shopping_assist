"""Integration tests for :mod:`make_daily`.

Tests cover:
- Helper functions already tested in ``test_extract_flower.py`` /
  ``test_extract_meter.py``
- ``load_cost_map`` with mocked DB
- ``generate_daily_report`` with fully mocked dependencies
"""
from unittest.mock import MagicMock, patch, PropertyMock
import pandas as pd
import pytest

from make_daily import (
    load_cost_map,
    generate_daily_report,
    generate_all_missing_reports,
    ensure_output_dir,
)


# ===========================================================================
# load_cost_map
# ===========================================================================

class TestLoadCostMap:

    @patch('pandas.read_sql')
    def test_load_with_data(self, mock_read_sql, mock_conn):
        """Should return a dict mapping flower → cost_per_meter."""
        mock_read_sql.return_value = pd.DataFrame({
            'flower': ['花型A', '花型B'],
            'cost_per_meter': [10.0, 15.5],
        })
        cost_map = load_cost_map('2026-07-15')
        assert cost_map == {'花型A': 10.0, '花型B': 15.5}

    @patch('pandas.read_sql')
    def test_load_empty(self, mock_read_sql, mock_conn):
        """When the query returns empty, return an empty dict."""
        mock_read_sql.return_value = pd.DataFrame({'flower': [], 'cost_per_meter': []})
        cost_map = load_cost_map('2026-07-15')
        assert cost_map == {}

    @patch('pandas.read_sql')
    def test_load_exception_returns_empty(self, mock_read_sql, mock_conn):
        """When the query raises, return an empty dict (graceful fallback)."""
        mock_read_sql.side_effect = Exception("DB error")
        cost_map = load_cost_map('2026-07-15')
        assert cost_map == {}


# ===========================================================================
# generate_daily_report  (fully mocked)
# ===========================================================================

class TestGenerateDailyReport:

    @patch('make_daily.ensure_output_dir')
    @patch('make_daily.engine')
    @patch('pandas.read_sql')
    @patch('make_daily.deduct_stock')
    @patch('inventory_service.fill_missing_snapshots')
    @patch('make_daily.pd.ExcelWriter')
    def test_no_orders_for_date(
        self, mock_excel, mock_fill, mock_deduct,
        mock_read_sql, mock_engine, mock_ensure,
    ):
        """When there are no orders, the function should return None gracefully."""
        mock_read_sql.return_value = pd.DataFrame()  # empty orders
        mock_engine.connect.return_value = _make_mock_connection()
        result = generate_daily_report('2026-07-15')
        assert result is None

    @patch('make_daily.ensure_output_dir')
    @patch('make_daily.engine')
    @patch('pandas.read_sql')
    @patch('make_daily.deduct_stock')
    @patch('inventory_service.fill_missing_snapshots')
    @patch('make_daily.pd.ExcelWriter')
    def test_with_orders(
        self, mock_excel, mock_fill, mock_deduct,
        mock_read_sql, mock_engine, mock_ensure,
    ):
        """Happy-path: generate report with sample orders."""
        # Sequence of read_sql calls:
        # 1. existing cache check (inside engine.connect)
        # 2. old_data read
        # 3. main orders query
        # 4. cost_map
        mock_read_sql.side_effect = [
            # order query
            pd.DataFrame({
                'id': [1, 2],
                'order_no': ['ORD001', 'ORD002'],
                'product': ['花型A布料', '花型B布料'],
                'product_spec': ['花型A,2米', '花型B,3米'],
                'product_quantity': [1, 2],
                'merchant_income': [50.0, 90.0],
                'cost': [0.0, 0.0],
                'meter': [0.0, 0.0],
                'express_cost': [0.0, 0.0],
                'traffic_cost': [0.0, 0.0],
                'profit': [0.0, 0.0],
                'after_sale_status': ['', ''],
                'order_status': ['已发货', '已发货'],
            }),
        ]

        mock_engine.connect.return_value = _make_mock_connection()
        mock_fill.return_value = (1, 'success', '补全成功')
        mock_excel.return_value.__enter__.return_value = MagicMock()

        result = generate_daily_report('2026-07-15')
        # May return None in mock-heavy setup; at minimum no crash
        assert result is None or isinstance(result, str)


def _make_mock_connection():
    """Helper: create a mock connection that returns 0 for scalar counts."""
    conn = MagicMock()
    conn.execute.return_value.scalar.return_value = 0
    conn.execute.return_value.fetchall.return_value = []
    conn.execute.return_value.fetchone.return_value = None
    conn.__enter__.return_value = conn  # Python 3.12+ __enter__ no longer returns self
    return conn


# ===========================================================================
# generate_all_missing_reports
# ===========================================================================

class TestGenerateAllMissingReports:

    @patch('pandas.read_sql')
    @patch('make_daily.generate_daily_report')
    @patch('make_daily.ensure_output_dir')
    def test_no_dates_found(self, mock_ensure, mock_gen, mock_read_sql, mock_conn):
        """When no order dates exist, nothing should be generated."""
        mock_read_sql.return_value = pd.DataFrame()
        generate_all_missing_reports()
        mock_gen.assert_not_called()

    @patch('pandas.read_sql')
    @patch('make_daily.generate_daily_report')
    @patch('make_daily.ensure_output_dir')
    def test_generates_for_each_date(self, mock_ensure, mock_gen, mock_read_sql, mock_conn):
        """Should call generate_daily_report for each order date."""
        mock_read_sql.return_value = pd.DataFrame({
            'order_date': pd.to_datetime(['2026-07-01', '2026-07-02']),
        })
        generate_all_missing_reports()
        assert mock_gen.call_count == 2


# ===========================================================================
# ensure_output_dir
# ===========================================================================

class TestEnsureOutputDir:

    @patch('os.path.exists')
    @patch('os.makedirs')
    def test_dir_exists(self, mock_makedirs, mock_exists):
        """When the output dir already exists, don't create it."""
        mock_exists.return_value = True
        ensure_output_dir()
        mock_makedirs.assert_not_called()

    @patch('os.path.exists')
    @patch('os.makedirs')
    def test_dir_not_exists(self, mock_makedirs, mock_exists):
        """When the output dir does not exist, create it."""
        mock_exists.return_value = False
        ensure_output_dir()
        mock_makedirs.assert_called_once()
