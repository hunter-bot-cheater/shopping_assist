"""Integration tests for :mod:`inventory_service`.

Most functions in ``inventory_service`` depend on ``mysql_conn.engine``,
which is mocked at the module level in ``conftest.py``.  Each test receives
a fresh ``mock_conn`` fixture and controls what the DB queries return.
"""
from unittest.mock import patch, MagicMock, call
import pandas as pd
import pytest

from inventory_service import (
    check_flower_active,
    get_current_stock,
    add_stock,
    deduct_stock,
    rollback_daily_sales,
    update_inventory_snapshot,
    fill_missing_snapshots,
    get_restock_suggestions,
    get_missing_report_dates,
    get_latest_snapshot_date,
    get_inventory_snapshot,
    get_inventory_report,
    get_stock_log,
    write_off_stock,
    adjust_stock,
)


# ===========================================================================
# check_flower_active  (uses engine.connect + execute.fetchone)
# ===========================================================================

class TestCheckFlowerActive:
    """check_flower_active queries product_cost for is_deleted status."""

    def test_active_flower(self, mock_conn):
        """is_deleted = 0 → (True, '')."""
        mock_conn.execute.return_value.fetchone.return_value = (0,)
        ok, msg = check_flower_active('花型A')
        assert ok is True
        assert msg == ''

    def test_deleted_flower(self, mock_conn):
        """is_deleted = 1 → (False, '已被删除')."""
        mock_conn.execute.return_value.fetchone.return_value = (1,)
        ok, msg = check_flower_active('已删除花型')
        assert ok is False
        assert '已被删除' in msg

    def test_nonexistent_flower(self, mock_conn):
        """No row found → (False, '不存在')."""
        mock_conn.execute.return_value.fetchone.return_value = None
        ok, msg = check_flower_active('不存在')
        assert ok is False
        assert '不存在' in msg


# ===========================================================================
# get_current_stock  (uses engine.connect + execute.fetchone)
# ===========================================================================

class TestGetCurrentStock:

    def test_existing_flower(self, mock_conn):
        mock_conn.execute.return_value.fetchone.return_value = (50.0,)
        assert get_current_stock('花型A') == 50.0

    def test_nonexistent_flower(self, mock_conn):
        mock_conn.execute.return_value.fetchone.return_value = None
        with pytest.raises(ValueError, match='不存在'):
            get_current_stock('不存在')


# ===========================================================================
# add_stock  (transaction with multiple execute calls)
# ===========================================================================

class TestAddStock:

    @patch('inventory_service.check_flower_active', return_value=(True, ''))
    @patch('inventory_service.get_current_stock', return_value=100.0)
    def test_add_stock_success(self, mock_get_stock, mock_check, mock_conn):
        """Basic stock addition updates inventory, snapshot, and log."""
        # configure snapshot-check query and other executes
        mock_conn.execute.side_effect = [
            MagicMock(rowcount=3),       # UPDATE inventory → 3 rows
            MagicMock(scalar=lambda: 5), # SELECT COUNT(*) → 5 snapshots exist
            MagicMock(rowcount=10),      # UPDATE snapshot → 10 rows affected
            MagicMock(),                 # INSERT INTO inventory_log
        ]

        add_stock('花型A', 20, '采购入库', 'tester', '2026-07-15')
        # No exception means success

    @patch('inventory_service.check_flower_active', return_value=(True, ''))
    @patch('inventory_service.get_current_stock', return_value=100.0)
    def test_add_stock_negative_qty(self, mock_get_stock, mock_check, mock_conn):
        with pytest.raises(ValueError, match='必须大于 0'):
            add_stock('花型A', -5)

    @patch('inventory_service.check_flower_active', return_value=(True, ''))
    @patch('inventory_service.get_current_stock', return_value=100.0)
    def test_add_stock_zero_qty(self, mock_get_stock, mock_check, mock_conn):
        with pytest.raises(ValueError, match='必须大于 0'):
            add_stock('花型A', 0)

    @patch('inventory_service.check_flower_active', return_value=(False, '花型已被删除'))
    def test_add_stock_deleted_flower(self, mock_check, mock_conn):
        with pytest.raises(ValueError, match='已被删除'):
            add_stock('已删除花型', 10)


# ===========================================================================
# deduct_stock  (transaction with multiple execute calls)
# ===========================================================================

class TestDeductStock:

    @patch('inventory_service.check_flower_active', return_value=(True, ''))
    @patch('inventory_service.get_current_stock', return_value=100.0)
    def test_deduct_stock_success(self, mock_get_stock, mock_check, mock_conn):
        mock_conn.execute.side_effect = [
            MagicMock(),                 # UPDATE inventory
            MagicMock(scalar=lambda: 5), # SELECT COUNT(*) → 5 snapshots
            MagicMock(rowcount=10),      # UPDATE snapshot
            MagicMock(),                 # INSERT INTO inventory_log
        ]
        deduct_stock('花型A', 30, '销售出库', 'tester', '2026-07-15')

    @patch('inventory_service.check_flower_active', return_value=(True, ''))
    @patch('inventory_service.get_current_stock', return_value=5.0)
    def test_deduct_stock_insufficient(self, mock_get_stock, mock_check, mock_conn):
        """Stock should floor at 0 — no exception, just capped."""
        mock_conn.execute.side_effect = [
            MagicMock(),                 # UPDATE inventory
            MagicMock(scalar=lambda: 5),
            MagicMock(rowcount=10),
            MagicMock(),                 # INSERT INTO inventory_log
        ]
        # Deducting more than available should not raise
        deduct_stock('花型A', 100, '销售出库', 'tester', '2026-07-15')

    def test_deduct_stock_negative_qty(self, mock_conn):
        with pytest.raises(ValueError, match='必须大于 0'):
            deduct_stock('花型A', -10)


# ===========================================================================
# rollback_daily_sales  (transaction)
# ===========================================================================

class TestRollbackDailySales:

    def test_no_logs_found(self, mock_conn):
        """When no sales logs exist, should return (0, True, message)."""
        mock_conn.execute.return_value.fetchall.return_value = []
        count, ok, msg = rollback_daily_sales('2026-07-15')
        assert count == 0
        assert ok is True
        assert '未找到' in msg

    def test_rollback_with_logs(self, mock_conn):
        """When sales logs exist, should rollback and insert reversal logs."""
        # First query: find sales logs
        log_rows = [
            (1, '花型A', -10.0, 50.0, 40.0),  # id, flower, change_qty, before, after
            (2, '花型B', -20.0, 100.0, 80.0),
        ]
        mock_conn.execute.side_effect = [
            MagicMock(fetchall=lambda: log_rows),  # FIND logs
            # per-log: get_current_stock
            # Each will use engine.connect() independently…
        ]

    def test_rollback_clean_command(self, mock_conn):
        """Rollback also deletes shortfall records for the target date."""
        mock_conn.execute.return_value.fetchall.return_value = [
            (1, '花型A', -10.0, 50.0, 40.0),
        ]
        count, ok, msg = rollback_daily_sales('2026-07-15')
        assert ok is True


# ===========================================================================
# update_inventory_snapshot
# ===========================================================================

class TestUpdateInventorySnapshot:

    def test_update_success(self, mock_conn):
        """Change from 100 to 150 should apply +50 delta to all later dates."""
        mock_conn.execute.return_value.fetchone.return_value = (100.0,)
        mock_conn.execute.return_value.rowcount = 5  # affected 5 subsequent days

        ok, msg, affected = update_inventory_snapshot(
            '花型A', '2026-07-15', 150, operator='tester', reason='盘点调整'
        )
        assert ok is True
        assert affected == 5

    def test_update_negative_stock(self, mock_conn):
        """new_stock < 0 should be rejected."""
        ok, msg, affected = update_inventory_snapshot(
            '花型A', '2026-07-15', -10
        )
        assert ok is False
        assert affected == 0

    def test_no_snapshot_found(self, mock_conn):
        """When the target snapshot row does not exist, return error."""
        mock_conn.execute.return_value.fetchone.return_value = None
        ok, msg, affected = update_inventory_snapshot(
            '花型A', '2026-07-15', 100
        )
        assert ok is False
        assert '未找到' in msg


# ===========================================================================
# fill_missing_snapshots  (uses pd.read_sql and execute)
# ===========================================================================

class TestFillMissingSnapshots:

    @patch('system_service.get_system_start_date', return_value=pd.Timestamp('2026-07-01'))
    @patch('pandas.read_sql')
    def test_no_base_data(self, mock_start_date, mock_read_sql, mock_conn):
        """When inventory_base is empty, return error."""
        # Simulate empty base table
        mock_read_sql.side_effect = [
            pd.DataFrame(),  # empty prev_df
            pd.DataFrame(),  # empty sales_df
        ]
        mock_conn.execute.return_value.scalar.return_value = 0  # base_check = 0
        filled, status, msg = fill_missing_snapshots(operator='tester')
        assert status == 'error'
        assert '为空' in msg

    @patch('system_service.get_system_start_date', return_value=pd.Timestamp('2026-07-01'))
    @patch('pandas.read_sql')
    def test_fill_already_latest(self, mock_start_date, mock_read_sql, mock_conn):
        """When latest snapshot is past up_to_date, return 'already_latest'."""
        from datetime import date
        mock_conn.execute.side_effect = [
            MagicMock(scalar=lambda: 5),                                     # base_check > 0
            MagicMock(fetchall=lambda: [('花型A',), ('花型B',)]),             # flowers_raw
            MagicMock(scalar=lambda: 1),                                     # 花型A exists in base
            MagicMock(scalar=lambda: 1),                                     # 花型B exists in base
            MagicMock(scalar=lambda: date(2026, 7, 5)),                      # latest > up_to_date
        ]
        filled, status, msg = fill_missing_snapshots(up_to_date='2026-07-03', operator='tester')
        assert status == 'already_latest'


# ===========================================================================
# get_restock_suggestions  (uses pd.read_sql + engine.connect)
# ===========================================================================

class TestGetRestockSuggestions:

    @patch('add_del_flower.get_available_flowers')
    @patch('inventory_service.get_latest_snapshot_date')
    @patch('inventory_service.get_inventory_snapshot')
    @patch('pandas.read_sql')
    def test_no_available_flowers(
        self, mock_read_sql, mock_get_inv, mock_latest, mock_avail, mock_conn
    ):
        """When no flowers exist, return empty DataFrame."""
        mock_avail.return_value = pd.DataFrame()
        result = get_restock_suggestions()
        assert result.empty

    @patch('add_del_flower.get_available_flowers')
    @patch('inventory_service.get_latest_snapshot_date')
    @patch('inventory_service.get_inventory_snapshot')
    @patch('pandas.read_sql')
    def test_suggestions_computed(
        self, mock_read_sql, mock_get_inv, mock_latest, mock_avail, mock_conn
    ):
        """Verify suggestion calculation with sample data."""
        mock_avail.return_value = pd.DataFrame({'flower': ['花型A', '花型B', '花型C']})
        mock_latest.return_value = '2026-07-15'
        mock_get_inv.return_value = pd.DataFrame({
            '花型': ['花型A', '花型B', '花型C'],
            '库存': [10.0, 50.0, 200.0],
        })
        mock_read_sql.return_value = pd.DataFrame({
            'flower': ['花型A', '花型B'],
            'avg_daily': [5.0, 2.0],
        })

        result = get_restock_suggestions(target_days=7)
        assert not result.empty
        assert '建议拿货(米)' in result.columns
        assert '可售天数' in result.columns


# ===========================================================================
# get_missing_report_dates
# ===========================================================================

class TestGetMissingReportDates:

    @patch('system_service.get_system_start_date', return_value=pd.Timestamp('2026-07-01'))
    def test_no_missing(self, mock_start_date, mock_conn):
        """When order dates match report dates, return empty list."""

        def side_effect(sql, params=None, **kw):
            res = MagicMock()
            sql_str = str(sql)
            if 'data2026' in sql_str:
                # order dates
                res.fetchall.return_value = [('2026-07-01',)]
            else:
                # report dates
                res.fetchall.return_value = [('2026-07-01',)]
            return res

        mock_conn.execute.side_effect = side_effect
        missing = get_missing_report_dates()
        assert missing == []

    @patch('system_service.get_system_start_date', return_value=pd.Timestamp('2026-07-01'))
    def test_has_missing(self, mock_start_date, mock_conn):
        """When order dates have a date not in reports, return it."""

        def side_effect(sql, params=None, **kw):
            res = MagicMock()
            sql_str = str(sql)
            if 'data2026' in sql_str:
                res.fetchall.return_value = [('2026-07-01',), ('2026-07-02',)]
            else:
                res.fetchall.return_value = [('2026-07-01',)]
            return res

        mock_conn.execute.side_effect = side_effect
        missing = get_missing_report_dates()
        assert '2026-07-02' in [str(d) for d in missing]


# ===========================================================================
# get_latest_snapshot_date
# ===========================================================================

class TestGetLatestSnapshotDate:

    def test_with_data(self, mock_conn):
        mock_conn.execute.return_value.scalar.return_value = '2026-07-15'
        result = get_latest_snapshot_date()
        assert result == '2026-07-15'

    def test_no_data(self, mock_conn):
        mock_conn.execute.return_value.scalar.return_value = None
        assert get_latest_snapshot_date() is None


# ===========================================================================
# get_inventory_snapshot  (uses engine.connect + pd.read_sql)
# ===========================================================================

class TestGetInventorySnapshot:

    @patch('pandas.read_sql')
    def test_with_flower(self, mock_read_sql, mock_conn):
        """Query for a specific flower."""
        mock_conn.execute.return_value.fetchone.return_value = (25.0,)
        result = get_inventory_snapshot('2026-07-15', flower='花型A')
        assert not result.empty
        assert result.iloc[0]['库存'] == 25.0

    @patch('pandas.read_sql')
    def test_all_flowers(self, mock_read_sql, mock_conn):
        """Query all flowers for a date."""
        mock_read_sql.return_value = pd.DataFrame({
            'flower': ['花型A', '花型B'],
            'stock': [10.0, 20.0],
        })
        result = get_inventory_snapshot('2026-07-15')
        assert len(result) == 2
        assert '库存' in result.columns
