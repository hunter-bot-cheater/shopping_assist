"""Tests for :mod:`populate_refund_details`.

All DB interactions are mocked.
"""
from unittest.mock import MagicMock, patch
import pandas as pd
import pytest

from populate_refund_details import sync_refund_details


class TestSyncRefundDetails:

    @patch('pandas.read_sql')
    def test_sync_success(self, mock_read_sql, mock_conn):
        """Successful sync should complete without error."""
        mock_read_sql.return_value = pd.DataFrame({
            'order_no': ['ORD001'],
            'product': ['花型A布料'],
            'product_spec': ['花型A,2米'],
            'product_quantity': [1],
            'merchant_income': [50.0],
            'after_sale_status': ['已发货，退款成功'],
            'order_time': [pd.Timestamp('2026-07-15 10:00:00')],
        })

        # sync_refund_details will call execute to INSERT (UPSERT)
        mock_conn.execute.return_value.rowcount = 1

        # Should not raise
        sync_refund_details(cost_map={'花型A': 10.0})

        # 花型应被归一为成本表花型
        last_args, _ = mock_conn.execute.call_args
        params = last_args[1]
        assert params['flower'] == '花型A'

    @patch('pandas.read_sql')
    def test_sync_no_refund_orders(self, mock_read_sql, mock_conn):
        """When no refund orders exist, the function should still succeed."""
        mock_read_sql.return_value = pd.DataFrame()
        sync_refund_details(cost_map={'花型A': 10.0})

    @patch('pandas.read_sql')
    def test_normalizes_variant_flower_to_cost_table_name(self, mock_read_sql, mock_conn):
        """带括号后缀的变体花型名应归一为成本表花型名（如 3D立体太阳花（绵绸人棉100）→ 3D立体太阳花）。"""
        mock_read_sql.return_value = pd.DataFrame({
            'order_no': ['ORD001'],
            'product': ['3D立体太阳花布料'],
            'product_spec': ['3D立体太阳花（绵绸人棉100）,2米'],
            'product_quantity': [1],
            'merchant_income': [50.0],
            'after_sale_status': ['已发货，退款成功'],
            'order_time': [pd.Timestamp('2026-07-15 10:00:00')],
        })
        mock_conn.execute.return_value.rowcount = 1

        sync_refund_details(cost_map={'3D立体太阳花': 10.0})

        last_args, _ = mock_conn.execute.call_args
        params = last_args[1]
        assert params['flower'] == '3D立体太阳花'
