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
            'product_spec': ['花型A,2米'],
            'product_quantity': [1],
            'merchant_income': [50.0],
            'after_sale_status': ['已发货，退款成功'],
            'order_time': [pd.Timestamp('2026-07-15 10:00:00')],
        })

        # sync_refund_details will call execute to DELETE + INSERT
        mock_conn.execute.return_value.rowcount = 1

        # Should not raise
        sync_refund_details()

    @patch('pandas.read_sql')
    def test_sync_no_refund_orders(self, mock_read_sql, mock_conn):
        """When no refund orders exist, the function should still succeed."""
        mock_read_sql.return_value = pd.DataFrame()
        sync_refund_details()
