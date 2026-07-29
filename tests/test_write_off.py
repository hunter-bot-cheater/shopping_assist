"""Tests for :func:`inventory_service.write_off_stock`.

Tests the stock write-off (报损) functionality with mocked DB.
"""
from unittest.mock import MagicMock, patch
import pytest

from inventory_service import write_off_stock


class TestWriteOffStock:

    @patch('inventory_service.check_flower_active', return_value=(True, ''))
    def test_write_off_success(self, mock_check, mock_conn):
        """Happy-path: deduct stock and log the write-off."""
        mock_conn.execute.return_value.fetchone.return_value = (100.0,)
        mock_conn.execute.return_value.rowcount = 1

        # Should not raise
        write_off_stock('花型A', 10, '裁剪损耗', 'tester')

    @patch('inventory_service.check_flower_active', return_value=(True, ''))
    def test_write_off_negative_qty(self, mock_check, mock_conn):
        with pytest.raises(ValueError, match='必须大于 0'):
            write_off_stock('花型A', -5)

    @patch('inventory_service.check_flower_active', return_value=(True, ''))
    def test_write_off_insufficient_stock(self, mock_check, mock_conn):
        """When stock is less than write-off qty, raise ValueError."""
        mock_conn.execute.return_value.fetchone.return_value = (10.0,)
        with pytest.raises(ValueError, match='库存不足'):
            write_off_stock('花型A', 50)

    @patch('inventory_service.check_flower_active', return_value=(False, '花型已被删除'))
    def test_write_off_deleted_flower(self, mock_check, mock_conn):
        with pytest.raises(ValueError, match='已被删除'):
            write_off_stock('已删除花型', 10)
