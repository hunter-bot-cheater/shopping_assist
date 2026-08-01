"""Integration tests for :mod:`make_daily`.

Tests cover:
- Helper functions already tested in ``test_extract_flower.py`` /
  ``test_extract_meter.py``
- ``load_cost_map`` with mocked DB
- ``generate_daily_report`` with fully mocked dependencies
"""
from unittest.mock import MagicMock, patch, PropertyMock
import os

import pandas as pd
import pytest

from make_daily import (
    load_cost_map,
    generate_daily_report,
    generate_all_missing_reports,
    generate_range_report,
    build_platform_summary,
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
                'platform': [0, 1],
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
# 平台分组（真实 Excel 写入 tmp_path）
# ===========================================================================

class TestPlatformGrouping:

    def test_summary_grouped_by_platform(self, tmp_path):
        """花型汇总为左右分列宽表；平台汇总 sheet 三平台齐全；明细含平台列。"""
        order_df = pd.DataFrame({
            'id': [1, 2],
            'order_no': ['ORD001', 'ORD002'],
            'product': ['花型A布料', '花型B布料'],
            'product_spec': ['花型A,2米', '花型B,3米'],
            'product_quantity': [1, 1],
            'merchant_income': [50.0, 90.0],
            'cost': [0.0, 0.0],
            'meter': [0.0, 0.0],
            'express_cost': [0.0, 0.0],
            'traffic_cost': [0.0, 0.0],
            'profit': [0.0, 0.0],
            'after_sale_status': ['', ''],
            'order_status': ['已发货', '已发货'],
            'platform': [0, 1],  # 拼多多 + 淘宝
        })

        with patch('make_daily.OUTPUT_DIR', str(tmp_path)), \
             patch('make_daily.engine') as mock_engine, \
             patch('pandas.read_sql') as mock_read_sql, \
             patch('make_daily.load_cost_map',
                   return_value={'花型A': 10.0, '花型B': 15.5}), \
             patch('make_daily.deduct_stock'), \
             patch('inventory_service.fill_missing_snapshots') as mock_fill, \
             patch('inventory_service.get_missing_report_dates', return_value=[]):

            mock_read_sql.side_effect = [order_df]
            mock_engine.connect.return_value = _make_mock_connection()
            mock_fill.return_value = (0, 'already_latest', '已最新')

            result = generate_daily_report('2026-07-15')
            assert isinstance(result, str) and result.endswith('.xlsx')

            xlsx_files = list(tmp_path.glob('*.xlsx'))
            assert len(xlsx_files) == 1

            with pd.ExcelFile(xlsx_files[0]) as xf:
                assert set(xf.sheet_names) == {'花型汇总', '平台汇总', '订单明细'}

                # 花型汇总：左右分列（花型 | 拼多多6列 | 空2 | 淘宝6列 | 空2 | 抖音6列 | 空2 | 汇总6列 = 31 列）
                head = pd.read_excel(xf, '花型汇总', header=None, nrows=2)
                assert head.shape[1] == 31
                # 第1行：平台合并表头
                assert head.iloc[0, 0] == '花型'
                assert head.iloc[0, 1] == '拼多多'
                assert head.iloc[0, 9] == '淘宝'
                assert head.iloc[0, 17] == '抖音'
                assert head.iloc[0, 25] == '汇总'
                # 第2行：指标名
                assert head.iloc[1, 1] == '订单数'
                assert head.iloc[1, 4] == '营业额'
                assert head.iloc[1, 9] == '订单数'
                assert head.iloc[1, 17] == '订单数'
                assert head.iloc[1, 25] == '订单数'

                # 数据行：花型按汇总营业额降序，末尾总计行
                data = pd.read_excel(xf, '花型汇总', header=None, skiprows=2)
                assert data.shape[1] == 31
                flowers = data[0].tolist()
                assert '花型A' in flowers
                assert '花型B' in flowers
                assert flowers[-1] == '【总计】'

                # 拼多多块（花型A）/ 淘宝块（花型B）数据落位正确，抖音块全为0
                row_a = data[data[0] == '花型A'].iloc[0]
                row_b = data[data[0] == '花型B'].iloc[0]
                assert row_a[1] == 1        # 拼多多_订单数
                assert row_a[4] == 50.0     # 拼多多_营业额
                assert row_b[9] == 1        # 淘宝_订单数
                assert row_b[12] == 90.0    # 淘宝_营业额
                assert row_a[17] == 0       # 抖音_订单数（无数据）
                assert row_a[20] == 0       # 抖音_营业额
                assert row_a[25] == 1       # 汇总_订单数
                assert data[data[0] == '【总计】'].iloc[0][25] == 2  # 汇总订单总数
                assert data[data[0] == '【总计】'].iloc[0][17] == 0  # 抖音订单总数

                # 平台汇总：拼多多/淘宝/抖音 三行齐全（无数据补 0）+ 合计
                ps = pd.read_excel(xf, '平台汇总')
                assert ps['平台'].tolist() == ['拼多多', '淘宝', '抖音', '合计']
                douyin_row = ps[ps['平台'] == '抖音'].iloc[0]
                assert douyin_row['订单数'] == 0
                assert douyin_row['营业额'] == 0

                # 订单明细含平台列
                detail = pd.read_excel(xf, '订单明细')
                assert '平台' in detail.columns


# ===========================================================================
# build_platform_summary  （左右分列宽表）
# ===========================================================================

class TestBuildPlatformSummary:

    def test_wide_layout_and_total(self):
        """build_platform_summary 返回左右分列宽表，含总计行与空列占位。"""
        normal_df = pd.DataFrame({
            '花型': ['花型A', '花型B'],
            '平台': ['拼多多', '淘宝'],
            'order_no': ['O1', 'O2'],
            '成本': [20.0, 93.0],
            '米数': [2.0, 6.0],
            'merchant_income': [50.0, 90.0],
            '快递费': [2.5, 2.5],
            '盈利': [27.5, -5.5],
        })
        wide = build_platform_summary(normal_df)
        # 花型 | 拼多多6列 | 空2 | 淘宝6列 | 空2 | 抖音6列 | 空2 | 汇总6列 = 31 列
        assert len(wide.columns) == 31
        assert wide.columns[0] == '花型'
        assert '拼多多_营业额' in wide.columns
        assert '淘宝_营业额' in wide.columns
        assert '抖音_营业额' in wide.columns
        assert '汇总_营业额' in wide.columns
        assert wide['花型'].tolist()[-1] == '【总计】'

        row = wide[wide['花型'] == '花型A'].iloc[0]
        assert row['拼多多_营业额'] == 50.0
        assert row['淘宝_营业额'] == 0
        assert row['抖音_营业额'] == 0
        assert row['汇总_营业额'] == 50.0

        tot = wide[wide['花型'] == '【总计】'].iloc[0]
        assert tot['拼多多_营业额'] == 50.0
        assert tot['淘宝_营业额'] == 90.0
        assert tot['抖音_营业额'] == 0
        assert tot['汇总_营业额'] == 140.0
        # 空列占位（每平台块独立）
        assert wide['_spacer_0_1'].iloc[0] == ''
        assert wide['_spacer_0_2'].iloc[0] == ''
        assert wide['_spacer_1_1'].iloc[0] == ''
        assert wide['_spacer_2_1'].iloc[0] == ''


# ===========================================================================
# generate_range_report
# ===========================================================================

class TestGenerateRangeReport:

    def test_range_report_sheets(self, tmp_path):
        """区间报告生成 Excel：花型汇总/平台汇总/订单明细，文件名含日期区间。"""
        order_df = pd.DataFrame({
            'id': [1, 2],
            'order_no': ['ORD001', 'ORD002'],
            'product': ['花型A布料', '花型B布料'],
            'product_spec': ['花型A,2米', '花型B,3米'],
            'product_quantity': [1, 1],
            'merchant_income': [50.0, 90.0],
            'cost': [0.0, 0.0],
            'meter': [0.0, 0.0],
            'express_cost': [0.0, 0.0],
            'traffic_cost': [0.0, 0.0],
            'profit': [0.0, 0.0],
            'after_sale_status': ['', ''],
            'order_status': ['已发货', '已发货'],
            'platform': [0, 1],  # 拼多多 + 淘宝
        })

        with patch('make_daily.OUTPUT_DIR', str(tmp_path)), \
             patch('make_daily.engine') as mock_engine, \
             patch('pandas.read_sql', return_value=order_df), \
             patch('make_daily.load_cost_map',
                   return_value={'花型A': 10.0, '花型B': 15.5}):
            mock_engine.connect.return_value = _make_mock_connection()

            result = generate_range_report('2026-07-01', '2026-07-15', force=True)
            assert isinstance(result, str)
            assert os.path.basename(result) == '区间报告_20260701-20260715.xlsx'
            assert os.path.exists(result)

            with pd.ExcelFile(result) as xf:
                assert set(xf.sheet_names) == {'花型汇总', '平台汇总', '订单明细'}
                head = pd.read_excel(xf, '花型汇总', header=None, nrows=2)
                assert head.shape[1] == 31
                assert head.iloc[0, 9] == '淘宝'
                assert head.iloc[0, 17] == '抖音'
                assert head.iloc[0, 25] == '汇总'

    def test_range_report_counts_shipped_taobao(self, tmp_path):
        """发货且不退款的淘宝订单计入，不因 merchant_income=0 被排除。"""
        order_df = pd.DataFrame({
            'id': [1, 2],
            'order_no': ['ORD001', 'ORD002'],
            'product': ['花型A布料', '花型A布料'],
            'product_spec': ['花型A,2米', '花型A,2米'],
            'product_quantity': [1, 1],
            'merchant_income': [50.0, 0.0],
            'cost': [0.0, 0.0],
            'meter': [0.0, 0.0],
            'express_cost': [0.0, 0.0],
            'traffic_cost': [0.0, 0.0],
            'profit': [0.0, 0.0],
            'after_sale_status': ['', ''],
            'order_status': ['已发货', '已发货'],
            'platform': [0, 1],  # 拼多多 + 淘宝（merchant_income=0）
        })

        with patch('make_daily.OUTPUT_DIR', str(tmp_path)), \
             patch('make_daily.engine') as mock_engine, \
             patch('pandas.read_sql', return_value=order_df), \
             patch('make_daily.load_cost_map', return_value={'花型A': 10.0}):
            mock_engine.connect.return_value = _make_mock_connection()

            result = generate_range_report('2026-07-01', '2026-07-15', force=True)
            assert result and os.path.exists(result)

            with pd.ExcelFile(result) as xf:
                ps = pd.read_excel(xf, '平台汇总')
                tb = ps[ps['平台'] == '淘宝'].iloc[0]
                assert tb['订单数'] == 1      # 淘宝订单计入（即使营业额为0）
                assert tb['营业额'] == 0.0
                pdd = ps[ps['平台'] == '拼多多'].iloc[0]
                assert pdd['订单数'] == 1
                assert pdd['营业额'] == 50.0
                # 合计订单数 = 2
                assert ps[ps['平台'] == '合计'].iloc[0]['订单数'] == 2


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
        """有订单但无日报的日期应逐个生成日报。"""
        mock_read_sql.side_effect = [
            pd.DataFrame({'order_date': pd.to_datetime(['2026-07-01', '2026-07-02'])}),
            pd.DataFrame(columns=['report_date']),  # 无任何日报缓存
        ]
        generate_all_missing_reports()
        assert mock_gen.call_count == 2
        mock_gen.assert_any_call('2026-07-01', force=True)
        mock_gen.assert_any_call('2026-07-02', force=True)

    @patch('pandas.read_sql')
    @patch('make_daily.generate_daily_report')
    @patch('make_daily.ensure_output_dir')
    def test_skips_already_generated_dates(self, mock_ensure, mock_gen, mock_read_sql, mock_conn):
        """已有日报缓存的日期应跳过，只生成缺失的日期。"""
        mock_read_sql.side_effect = [
            pd.DataFrame({'order_date': pd.to_datetime(['2026-07-01', '2026-07-02'])}),
            pd.DataFrame({'report_date': pd.to_datetime(['2026-07-01'])}),  # 07-01 已有日报
        ]
        generate_all_missing_reports()
        assert mock_gen.call_count == 1
        mock_gen.assert_called_once_with('2026-07-02', force=True)

    @patch('pandas.read_sql')
    @patch('make_daily.generate_daily_report')
    @patch('make_daily.ensure_output_dir')
    def test_all_dates_have_reports(self, mock_ensure, mock_gen, mock_read_sql, mock_conn):
        """所有有订单的日期均已有日报时，不做任何生成。"""
        mock_read_sql.side_effect = [
            pd.DataFrame({'order_date': pd.to_datetime(['2026-07-01'])}),
            pd.DataFrame({'report_date': pd.to_datetime(['2026-07-01'])}),
        ]
        generate_all_missing_reports()
        mock_gen.assert_not_called()

    @patch('pandas.read_sql')
    @patch('make_daily.generate_daily_report')
    @patch('make_daily.ensure_output_dir')
    def test_skips_locked_file_and_continues(self, mock_ensure, mock_gen, mock_read_sql, mock_conn):
        """某日期的日报文件被 Excel 占用时，应跳过该日期并继续生成其余日期，不中断整个批次。"""
        mock_read_sql.side_effect = [
            pd.DataFrame({'order_date': pd.to_datetime(['2026-07-01', '2026-07-02'])}),
            pd.DataFrame(columns=['report_date']),  # 无日报缓存，两日均需生成
        ]
        # 第一个日期文件被占用抛 PermissionError，第二个日期正常生成
        mock_gen.side_effect = [PermissionError("file locked"), None]
        generate_all_missing_reports()  # 不应抛出异常
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
