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
    detect_platform,
    gen_taobao_after_sale_status,
    PLATFORM_PDD,
    PLATFORM_TAOBAO,
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

    @patch('import_order.ensure_platform_column', return_value=(False, "自动迁移失败，请手动运行 python migrate_platform.py"))
    def test_auto_migrate_failure(self, mock_ensure, mock_conn):
        """自动迁移失败时，应返回明确错误提示而非原始 SQL 错误。"""
        df = pd.DataFrame({'订单号': ['A'], '商品': ['B'], '商家实收金额(元)': [1.0]})
        result = import_excel_from_dataframe(df)
        assert result['success'] is False
        assert '迁移' in result['message']

    @patch('import_order.platform_column_exists', return_value=False)
    @patch('import_order.engine')
    def test_auto_migrate_platform_column(self, mock_engine, mock_platform_column_exists):
        """缺 platform 列时自动执行 ALTER 加列，再继续正常导入。"""
        df = pd.DataFrame({
            '订单号': ['ORD001', 'ORD002'],
            '商品': ['花型A布料', '花型B布料'],
            '商家实收金额(元)': [50.0, 80.0],
            '商品规格': ['规格A', '规格B'],
        })
        mock_conn = MagicMock(name='conn')
        mock_engine.begin.return_value.__enter__.return_value = mock_conn

        def side_effect(sql, params=None, **kw):
            result = MagicMock()
            result.rowcount = 1
            return result

        mock_conn.execute.side_effect = side_effect

        result = import_excel_from_dataframe(df, 'test.xlsx')
        assert result['success'] is True
        # 确认执行过 ALTER ... ADD COLUMN platform
        executed = [str(c.args[0]) for c in mock_conn.execute.call_args_list]
        assert any('ADD COLUMN' in s and 'platform' in s for s in executed)

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


# ===========================================================================
# detect_platform
# ===========================================================================

class TestDetectPlatform:

    def test_detect_pdd(self):
        df = pd.DataFrame({'订单号': ['A'], '商品': ['B'], '商家实收金额(元)': [1.0]})
        assert detect_platform(df) == PLATFORM_PDD

    def test_detect_taobao(self):
        df = pd.DataFrame({'订单编号': ['A'], '商品标题': ['B']})
        assert detect_platform(df) == PLATFORM_TAOBAO

    def test_detect_taobao_priority(self):
        """同时含拼多多和淘宝特征列时，淘宝优先（配置顺序保证）。"""
        df = pd.DataFrame({
            '订单号': ['A'], '商品': ['B'], '商家实收金额(元)': [1.0],
            '订单编号': ['T'], '商品标题': ['T商品'],
        })
        assert detect_platform(df) == PLATFORM_TAOBAO

    def test_detect_unknown_defaults_pdd(self):
        """无任何平台特征列时，默认按拼多多处理。"""
        df = pd.DataFrame({'某列': [1], '另一列': [2]})
        assert detect_platform(df) == PLATFORM_PDD


# ===========================================================================
# gen_taobao_after_sale_status（纯函数）
# ===========================================================================

class TestTaobaoAfterSaleStatus:
    """根据 订单状态×退款金额×发货/收货时间 合成淘宝售后状态。"""

    def _row(self, order_status='交易成功', refund=0, delivery=None, receive=None, close=''):
        return {
            '订单状态': order_status,
            '_temp_refund_amount': refund,
            '发货时间': delivery,
            '确认收货时间': receive,
            '_temp_close_reason': close,
        }

    def test_success_no_refund(self):
        assert gen_taobao_after_sale_status(self._row()) is None

    def test_refund_not_delivered(self):
        row = self._row(order_status='交易关闭', refund=20.0, delivery=None, receive=None)
        assert gen_taobao_after_sale_status(row) == '未发货，退款成功'

    def test_refund_delivered_not_received(self):
        row = self._row(order_status='交易关闭', refund=20.0,
                        delivery=pd.Timestamp('2026-07-15'), receive=None)
        assert gen_taobao_after_sale_status(row) == '已发货，退款成功'

    def test_refund_received(self):
        row = self._row(order_status='交易关闭', refund=20.0,
                        delivery=pd.Timestamp('2026-07-15'), receive=pd.Timestamp('2026-07-16'))
        assert gen_taobao_after_sale_status(row) == '已收货，退款成功'

    def test_close_reason_contains_refund(self):
        """订单状态非交易关闭，但关闭原因含退款且有退款金额，也应识别。"""
        row = self._row(order_status='交易成功', refund=10.0,
                        delivery=pd.Timestamp('2026-07-15'), receive=None,
                        close='买家申请退款')
        assert gen_taobao_after_sale_status(row) == '已发货，退款成功'

    def test_no_refund_no_close_reason(self):
        row = self._row(order_status='交易关闭', refund=0, close='')
        assert gen_taobao_after_sale_status(row) is None


# ===========================================================================
# 淘宝导入（平台自动识别 + 持久化）
# ===========================================================================

class TestTaobaoImport:

    @patch('import_order.engine')
    def test_taobao_import_success(self, mock_engine, mock_conn):
        """淘宝列格式应自动识别并成功导入，INSERT 需带 platform 列。"""
        df = pd.DataFrame({
            '订单编号': ['TB001', 'TB002'],
            '商品标题': ['花型A布料', '花型B布料'],
            '买家应付货款': [50.0, 80.0],
            '订单状态': ['交易成功', '交易成功'],
            '发货时间': ['2026-07-15 10:00:00', '2026-07-15 11:00:00'],
            '确认收货时间': ['2026-07-16 10:00:00', '2026-07-16 11:00:00'],
        })
        mock_engine.begin.return_value.__enter__.return_value = mock_conn

        result = import_excel_from_dataframe(df, 'taobao.xlsx')
        assert result['success'] is True
        assert result['stats']['成功导入'] == 2

        # 动态 INSERT 语句应包含 platform 列（值为 1）
        insert_sqls = [str(c.args[0]) for c in mock_conn.execute.call_args_list]
        assert any('platform' in s for s in insert_sqls)

    @patch('import_order.engine')
    def test_taobao_missing_income_column(self, mock_engine, mock_conn):
        """淘宝文件缺金额列时，映射后应报缺少必要列。"""
        df = pd.DataFrame({
            '订单编号': ['TB001'],
            '商品标题': ['花型A布料'],
            '订单状态': ['交易成功'],
        })
        mock_engine.begin.return_value.__enter__.return_value = mock_conn
        result = import_excel_from_dataframe(df, 'taobao.xlsx')
        assert result['success'] is False
        assert '缺少必要列' in result['message']
