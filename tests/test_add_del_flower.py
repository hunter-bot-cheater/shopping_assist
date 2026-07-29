"""Integration tests for :mod:`add_del_flower`.

All DB interactions are routed through the mocked ``mysql_conn.engine``
provided by ``conftest.py``.
"""
from unittest.mock import MagicMock, patch, call
import pandas as pd
import pytest

from add_del_flower import (
    get_all_flowers,
    get_available_flowers,
    add_flower,
    delete_flower,
    restore_flower,
)


# ===========================================================================
# get_all_flowers  /  get_available_flowers
# ===========================================================================

class TestGetAllFlowers:

    @patch('pandas.read_sql')
    def test_include_deleted(self, mock_read_sql, mock_conn):
        """include_deleted=True should return ALL flowers."""
        mock_read_sql.return_value = pd.DataFrame({
            'flower': ['花型A', '花型B', '已删除花型'],
            'cost_per_meter': [10.0, 15.0, 5.0],
            'is_deleted': [0, 0, 1],
        })
        result = get_all_flowers(include_deleted=True)
        assert len(result) == 3

    @patch('pandas.read_sql')
    def test_exclude_deleted(self, mock_read_sql, mock_conn):
        """include_deleted=False should only return active flowers."""
        mock_read_sql.return_value = pd.DataFrame({
            'flower': ['花型A', '花型B'],
            'cost_per_meter': [10.0, 15.0],
        })
        result = get_all_flowers(include_deleted=False)
        assert len(result) == 2
        assert 'is_deleted' not in result.columns


class TestGetAvailableFlowers:

    @patch('pandas.read_sql')
    def test_returns_only_active(self, mock_read_sql, mock_conn):
        mock_read_sql.return_value = pd.DataFrame({
            'flower': ['花型A', '花型B'],
            'cost_per_meter': [10.0, 15.0],
        })
        result = get_available_flowers()
        assert len(result) == 2
        assert 'flower' in result.columns

    @patch('pandas.read_sql')
    def test_empty_when_no_active(self, mock_read_sql, mock_conn):
        mock_read_sql.return_value = pd.DataFrame()
        result = get_available_flowers()
        assert result.empty


# ===========================================================================
# add_flower
# ===========================================================================

class TestAddFlower:

    def test_empty_name(self, mock_conn):
        ok, msg = add_flower('  ')
        assert ok is False
        assert '不能为空' in msg

    def test_add_success(self, mock_conn):
        """Happy-path: new flower insertion with all related tables."""
        # Chained execute calls inside transaction:
        # 1. SELECT is_deleted → None (new flower)
        # 2. INSERT product_cost
        # 3. INSERT inventory
        # 4. SELECT MIN(base_date) → returns date
        # 5. INSERT inventory_base
        # 6. SELECT MAX(snapshot_date) → returns None (no snapshots yet)
        # 7. INSERT inventory_log
        mock_conn.execute.side_effect = [
            MagicMock(fetchone=lambda: None),             # not existing
            MagicMock(),                                   # INSERT product_cost
            MagicMock(),                                   # INSERT inventory
            MagicMock(scalar=lambda: pd.Timestamp('2026-07-01')),  # MIN(base_date)
            MagicMock(),                                   # INSERT inventory_base
            MagicMock(scalar=lambda: None),                # MAX(snapshot)
            MagicMock(),                                   # INSERT inventory_log
        ]

        ok, msg = add_flower('新花型', 12.5, 'tester')
        assert ok is True
        assert '新增成功' in msg

    def test_add_existing_flower(self, mock_conn):
        """Adding a flower that already exists (active) → error."""
        mock_conn.execute.return_value.fetchone.return_value = (0,)  # is_deleted=0
        ok, msg = add_flower('花型A', 10.0, 'tester')
        assert ok is False
        assert '已存在' in msg

    def test_add_restore_deleted_flower(self, mock_conn):
        """Adding a previously deleted flower → auto-restore."""
        mock_conn.execute.return_value.fetchone.return_value = (1,)  # is_deleted=1
        mock_conn.execute.return_value.rowcount = 1

        ok, msg = add_flower('已删除花型', 10.0, 'tester')
        assert ok is True
        assert '恢复' in msg

    def test_transaction_rollback_on_error(self, mock_conn):
        """When an execute fails, the transaction should abort."""
        mock_conn.execute.side_effect = [
            MagicMock(fetchone=lambda: None),  # not existing
            Exception("DB error"),             # INSERT fails
        ]

        ok, msg = add_flower('新花型', 10.0, 'tester')
        assert ok is False
        assert '失败' in msg


# ===========================================================================
# delete_flower
# ===========================================================================

class TestDeleteFlower:

    def test_delete_empty_name(self, mock_conn):
        ok, msg = delete_flower('')
        assert ok is False

    def test_delete_success(self, mock_conn):
        """Happy-path: soft-delete sets is_deleted=1 and logs."""
        mock_conn.execute.side_effect = [
            MagicMock(fetchone=lambda: (0,)),               # is_deleted=0
            MagicMock(fetchone=lambda: (50.0,)),            # current_stock
            MagicMock(fetchone=lambda: (10.0,)),             # cost_per_meter
            MagicMock(),                                     # UPDATE product_cost
            MagicMock(),                                     # INSERT inventory_log
        ]
        ok, msg = delete_flower('花型A', 'tester')
        assert ok is True
        assert '已删除' in msg

    def test_delete_nonexistent(self, mock_conn):
        mock_conn.execute.return_value.fetchone.return_value = None
        ok, msg = delete_flower('不存在', 'tester')
        assert ok is False
        assert '不存在' in msg

    def test_delete_already_deleted(self, mock_conn):
        mock_conn.execute.return_value.fetchone.return_value = (1,)
        ok, msg = delete_flower('已删除花型', 'tester')
        assert ok is False
        assert '已处于删除状态' in msg


# ===========================================================================
# restore_flower
# ===========================================================================

class TestRestoreFlower:

    def test_restore_empty_name(self, mock_conn):
        """Empty flower name → failure."""
        ok, msg = restore_flower('')
        assert ok is False
        assert '请选择' in msg

    def test_restore_success(self, mock_conn):
        """Happy-path: clear is_deleted, log the restoration."""
        mock_conn.execute.side_effect = [
            MagicMock(fetchone=lambda: (1, pd.Timestamp('2026-07-10'))),  # is_deleted=1
            MagicMock(),                                                   # UPDATE
            MagicMock(fetchone=lambda: (30.0,)),                           # current_stock
            MagicMock(),                                                   # INSERT log
        ]
        ok, msg = restore_flower('已删除花型', 'tester')
        assert ok is True
        assert '已恢复' in msg

    def test_restore_not_deleted(self, mock_conn):
        """Active flower → already not deleted."""
        mock_conn.execute.return_value.fetchone.return_value = (0,)
        ok, msg = restore_flower('花型A', 'tester')
        assert ok is False
        assert '未被删除' in msg

    def test_restore_nonexistent(self, mock_conn):
        mock_conn.execute.return_value.fetchone.return_value = None
        ok, msg = restore_flower('不存在', 'tester')
        assert ok is False
        assert '不存在' in msg
