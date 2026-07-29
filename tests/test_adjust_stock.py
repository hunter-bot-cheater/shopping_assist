"""Tests for :func:`inventory_service.adjust_stock`.

Verifies the manual stock adjustment logic.
"""
from unittest.mock import MagicMock, patch
import pytest

from inventory_service import adjust_stock


class TestAdjustStock:

    @patch('inventory_service.check_flower_active', return_value=(True, ''))
    def test_increase_stock(self, mock_check, mock_conn):
        """Adjusting to a higher value should call UPDATE with positive diff."""
        mock_conn.execute.return_value.fetchone.return_value = (100.0,)
        mock_conn.execute.return_value.rowcount = 1

        before, after, diff, msg = adjust_stock('花型A', 150, '盘点调整', 'tester')
        assert before == 100.0
        assert after == 150.0
        assert diff == 50.0
        assert '增加' in msg

    @patch('inventory_service.check_flower_active', return_value=(True, ''))
    def test_decrease_stock(self, mock_check, mock_conn):
        """Adjusting to a lower value should call UPDATE with negative diff."""
        mock_conn.execute.return_value.fetchone.return_value = (100.0,)

        before, after, diff, msg = adjust_stock('花型A', 80, '盘点调整', 'tester')
        assert before == 100.0
        assert after == 80.0
        assert diff == -20.0
        assert '减少' in msg

    @patch('inventory_service.check_flower_active', return_value=(True, ''))
    def test_no_change(self, mock_check, mock_conn):
        """Adjusting to same value should return '无变化'."""
        mock_conn.execute.return_value.fetchone.return_value = (100.0,)

        before, after, diff, msg = adjust_stock('花型A', 100)
        assert diff == 0
        assert '无变化' in msg

    @patch('inventory_service.check_flower_active', return_value=(True, ''))
    def test_negative_target(self, mock_check, mock_conn):
        """Target stock cannot be negative."""
        with pytest.raises(ValueError, match='不能为负数'):
            adjust_stock('花型A', -10)

    def test_empty_flower_name(self, mock_conn):
        """Empty flower name should be rejected."""
        with pytest.raises(ValueError, match='不能为空'):
            adjust_stock('', 100)
