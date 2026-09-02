"""Integration tests for :mod:`inventory_service`.

Most functions in ``inventory_service`` depend on ``mysql_conn.engine``,
which is mocked at the module level in ``conftest.py``.  Each test receives
a fresh ``mock_conn`` fixture and controls what the DB queries return.
"""
from unittest.mock import patch, MagicMock, call
import pandas as pd
import pytest

from inventory_service import (
    _daily_adjust,
    check_flower_active,
    get_current_stock,
    add_stock,
    deduct_stock,
    rollback_inventory_to_date,
    sync_inventory_current_stock,
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


def make_side_effect(old_stock=None, latest=None, next_anchor=None,
                     sales_map=None, sync_stock=None, check_count=None):
    """
    构建一个基于 SQL 文本分发的 execute side_effect，覆盖锚点逻辑涉及的查询：
    旧快照读取 / 下一个锚点查找 / 最新快照日期 / 重算区间锚点检查 /
    日报销售 / 入库与调整合计 / current_stock 同步读取 / 快照数量检查。
    """
    def side_effect(sql, params=None, **kw):
        s = str(sql)
        res = MagicMock()
        # 旧快照读取（非锚点、非 MAX、非 ORDER BY）
        if ('SELECT stock FROM inventory_snapshot' in s and 'snapshot_date = :d' in s
                and 'ORDER BY' not in s and 'is_manual' not in s and 'MAX(' not in s):
            res.fetchone.return_value = (old_stock,) if old_stock is not None else None
        # 下一个锚点（is_manual=1 且 snapshot_date > :d）
        elif 'is_manual = 1' in s and 'snapshot_date > :d' in s and 'MIN(' not in s:
            res.fetchone.return_value = (next_anchor,) if next_anchor is not None else None
        # 最新快照日期
        elif 'MAX(snapshot_date)' in s:
            res.scalar.return_value = latest
        # 重算区间内锚点检查（snapshot_date = :d AND is_manual = 1）
        elif 'is_manual = 1' in s:
            res.fetchone.return_value = None
        # 日报销售
        elif 'daily_report_cache' in s:
            d = params.get('d') if params else None
            res.scalar.return_value = (sales_map or {}).get(d, 0)
        # 入库/调整合计
        elif 'inventory_log' in s and 'COALESCE' in s:
            res.scalar.return_value = 0
        # current_stock 同步读取
        elif 'ORDER BY snapshot_date DESC LIMIT 1' in s:
            res.fetchone.return_value = (sync_stock,) if sync_stock is not None else None
        # 快照数量检查（deduct_stock）
        elif 'COUNT(*)' in s and 'snapshot_date >= :d' in s:
            res.scalar.return_value = check_count if check_count is not None else 0
        else:
            res.rowcount = 1
        return res
    return side_effect


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
    def test_add_stock_success(self, mock_check, mock_conn):
        """Basic stock addition updates inventory, snapshot, and log."""
        # 新流程：读旧快照 → UPDATE 目标快照 → 查下一个锚点/最新日期 → INSERT log → 同步库存
        mock_conn.execute.side_effect = make_side_effect(
            old_stock=100.0, latest=None, sync_stock=120.0
        )
        add_stock('花型A', 20, '采购入库', 'tester', '2026-07-15')

    @patch('inventory_service.check_flower_active', return_value=(True, ''))
    def test_add_stock_per_day_recalc(self, mock_check, mock_conn):
        """入库后，后续快照应递减销售，而非机械 +qty。"""
        from datetime import date
        captured = []

        def side_effect(sql, params=None, **kw):
            if 'INSERT INTO inventory_snapshot' in str(sql) and 'ON DUPLICATE KEY' in str(sql):
                captured.append((params['d'], params['s']))
            return make_side_effect(
                old_stock=0.0,
                latest=date(2026, 7, 17),
                sales_map={date(2026, 7, 16): 10.0, date(2026, 7, 17): 5.0},
                sync_stock=85.0,
            )(sql, params, **kw)

        mock_conn.execute.side_effect = side_effect
        add_stock('花型A', 100, '采购入库', 'tester', '2026-07-15')
        # 7/15 旧值 0 → +100；7/16 = 100-10 = 90；7/17 = 90-5 = 85
        stock_by_day = dict(captured)
        assert stock_by_day[date(2026, 7, 16)] == 90.0
        assert stock_by_day[date(2026, 7, 17)] == 85.0

    @patch('inventory_service.check_flower_active', return_value=(True, ''))
    def test_add_stock_no_future_dates(self, mock_check, mock_conn):
        """无后续日期时不应报错。"""
        mock_conn.execute.side_effect = make_side_effect(
            old_stock=50.0, latest=None, sync_stock=80.0
        )
        add_stock('花型A', 30, '采购入库', 'tester', '2026-07-15')

    @patch('inventory_service.check_flower_active', return_value=(True, ''))
    def test_add_stock_stops_at_next_anchor(self, mock_check, mock_conn):
        """存在后续锚点时，只重算到锚点前一天，不越过锚点。"""
        from datetime import date
        captured = []
        next_anchor_date = date(2026, 7, 18)

        def side_effect(sql, params=None, **kw):
            if 'INSERT INTO inventory_snapshot' in str(sql) and 'ON DUPLICATE KEY' in str(sql):
                captured.append(params['d'])
            return make_side_effect(
                old_stock=100.0, next_anchor=next_anchor_date, sync_stock=120.0,
            )(sql, params, **kw)

        mock_conn.execute.side_effect = side_effect
        add_stock('花型A', 20, '采购入库', 'tester', '2026-07-15')
        # 重算区间 = (7/15, 7/17]，只生成 7/16、7/17，不生成锚点 7/18
        assert date(2026, 7, 16) in captured
        assert date(2026, 7, 17) in captured
        assert date(2026, 7, 18) not in captured

    @patch('inventory_service.check_flower_active', return_value=(True, ''))
    def test_add_stock_log_carries_effect_date(self, mock_check, mock_conn):
        """补录入库（target_date 为过去日期）时，流水写入 effect_date=target_date，
        且重算的入库/调整归集查询按 effect_date 匹配，而非按 created_at。"""
        from datetime import date
        captured_log = {}
        captured_sql = []

        def side_effect(sql, params=None, **kw):
            s = str(sql)
            captured_sql.append(s)
            if 'INSERT INTO inventory_log' in s:
                captured_log.update(params or {})
            return make_side_effect(
                old_stock=0.0, latest=date(2026, 7, 29), sync_stock=100.0,
            )(sql, params, **kw)

        mock_conn.execute.side_effect = side_effect
        add_stock('花型A', 100, '手动入库', 'tester', '2026-07-27')

        # 流水 INSERT 携带 effect_date = 补录的目标日期（而非执行当天）
        assert captured_log.get('eff') == date(2026, 7, 27)
        # 入库归集查询：COALESCE(effect_date, DATE(created_at)) = :d
        inbound_sqls = [s for s in captured_sql if 'inventory_log' in s and "change_type = '入库'" in s]
        assert inbound_sqls, "应调用 _daily_inbound 查询"
        assert 'COALESCE(effect_date, DATE(created_at)) = :d' in inbound_sqls[0]
        # 手动调整归集查询：同样按 effect_date 匹配（锚点调整不落入执行日）
        adjust_sqls = [s for s in captured_sql if 'inventory_log' in s and "change_type = '手动调整'" in s]
        assert adjust_sqls, "应调用 _daily_adjust 查询"
        assert 'COALESCE(effect_date, DATE(created_at)) = :d' in adjust_sqls[0]

    @patch('inventory_service.check_flower_active', return_value=(True, ''))
    def test_add_stock_negative_qty(self, mock_check, mock_conn):
        with pytest.raises(ValueError, match='必须大于 0'):
            add_stock('花型A', -5)

    @patch('inventory_service.check_flower_active', return_value=(True, ''))
    def test_add_stock_zero_qty(self, mock_check, mock_conn):
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
    def test_deduct_stock_success(self, mock_check, mock_conn):
        mock_conn.execute.side_effect = make_side_effect(
            check_count=5, old_stock=100.0, latest=None, sync_stock=70.0
        )
        deduct_stock('花型A', 30, '销售出库', 'tester', '2026-07-15')

    @patch('inventory_service.check_flower_active', return_value=(True, ''))
    def test_deduct_stock_insufficient(self, mock_check, mock_conn):
        """Stock should floor at 0 — no exception, just capped."""
        mock_conn.execute.side_effect = make_side_effect(
            check_count=5, old_stock=5.0, latest=None, sync_stock=0.0
        )
        # Deducting more than available should not raise
        deduct_stock('花型A', 100, '销售出库', 'tester', '2026-07-15')

    def test_deduct_stock_negative_qty(self, mock_conn):
        with pytest.raises(ValueError, match='必须大于 0'):
            deduct_stock('花型A', -10)


# ===========================================================================
# deduct_stock  日报路径（is_daily_sales=True）双重扣减回归测试
# ===========================================================================

class TestDeductStockDaily:
    """回归测试：日报自动扣减（is_daily_sales=True）不得双重扣减。
    快照补全逻辑已把当天销售烘焙进 snapshot[report_date]（end-of-day），
    deduct_stock 若再以该快照为基准扣 qty 会把当天销售减两次（粉红KT bug 根因）。
    修复后应以 snapshot[report_date-1] 为基准自愈重算。"""

    def _capture(self, mock_conn, old_stock=100.0, sync_stock=70.0, check_count=5, anchor_flag=None):
        calls = []

        def side_effect(sql, params=None, **kw):
            s = str(sql)
            res = MagicMock()
            calls.append((s, params or {}))
            if 'SELECT is_manual FROM inventory_snapshot' in s and 'snapshot_date = :d' in s:
                res.fetchone.return_value = None if anchor_flag is None else (anchor_flag,)
            elif ('SELECT stock FROM inventory_snapshot' in s and 'snapshot_date = :d' in s
                    and 'ORDER BY' not in s and 'is_manual' not in s and 'MAX(' not in s):
                res.fetchone.return_value = (old_stock,)
            elif 'is_manual = 1' in s and 'snapshot_date > :d' in s and 'MIN(' not in s:
                res.fetchone.return_value = None
            elif 'MAX(snapshot_date)' in s:
                res.scalar.return_value = None
            elif 'daily_report_cache' in s:
                res.scalar.return_value = 0
            elif 'inventory_log' in s and 'COALESCE' in s:
                res.scalar.return_value = 0
            elif 'ORDER BY snapshot_date DESC LIMIT 1' in s:
                res.fetchone.return_value = (sync_stock,)
            elif 'COUNT(*)' in s and 'snapshot_date >= :d' in s:
                res.scalar.return_value = check_count
            else:
                res.rowcount = 1
            return res

        mock_conn.execute.side_effect = side_effect
        return calls

    @patch('inventory_service.check_flower_active', return_value=(True, ''))
    def test_daily_path_bases_on_prev_day(self, mock_check, mock_conn):
        """日报路径应读取 snapshot[report_date-1] 作为基准，而非可能已烘焙的 report_date。"""
        calls = self._capture(mock_conn)
        deduct_stock('花型A', 30, '日报自动扣减 2026-07-15', 'system', '2026-07-15', is_daily_sales=True)
        reads = [p for s, p in calls
                 if 'SELECT stock FROM inventory_snapshot' in s and 'snapshot_date = :d' in s]
        assert reads, '应读取基准快照'
        assert str(reads[0]['d']) == '2026-07-14'

    @patch('inventory_service.check_flower_active', return_value=(True, ''))
    def test_daily_path_no_double_deduction(self, mock_check, mock_conn):
        """prev-day=100、当日销售=30 → 日报扣减后快照应为 70（而非 100-30-30=40）。"""
        calls = self._capture(mock_conn, old_stock=100.0, sync_stock=70.0)
        deduct_stock('花型A', 30, '日报自动扣减 2026-07-15', 'system', '2026-07-15', is_daily_sales=True)
        updates = [p for s, p in calls if 'INSERT INTO inventory_snapshot' in s]
        assert updates, '应写入/更新 report_date 快照'
        assert float(updates[0]['new']) == 70.0
        assert float(updates[0]['new2']) == 70.0

    @patch('inventory_service.check_flower_active', return_value=(True, ''))
    def test_manual_path_still_uses_same_day_base(self, mock_check, mock_conn):
        """手动路径（默认）仍以 report_date 快照为基准，行为不变。"""
        calls = self._capture(mock_conn)
        deduct_stock('花型A', 30, '手工出库', 'tester', '2026-07-15')
        reads = [p for s, p in calls
                 if 'SELECT stock FROM inventory_snapshot' in s and 'snapshot_date = :d' in s]
        assert reads, '应读取基准快照'
        assert str(reads[0]['d']) == '2026-07-15'

    @patch('inventory_service.check_flower_active', return_value=(True, ''))
    def test_daily_path_skips_manual_anchor(self, mock_check, mock_conn):
        """日报路径：report_date 为手动锚点（is_manual=1）时不得覆盖锚点快照。
        锚点是实盘数、已含当日销售，任何公式扣减都会破坏锚点（7/30 锚点被覆盖的 bug 根因）。"""
        calls = self._capture(mock_conn, anchor_flag=1)
        deduct_stock('花型A', 30, '日报自动扣减 2026-07-15', 'system', '2026-07-15', is_daily_sales=True)
        # 发起锚点检查
        anchor_checks = [p for s, p in calls if 'SELECT is_manual FROM inventory_snapshot' in s]
        assert anchor_checks, '应先检查 report_date 是否手动锚点'
        assert str(anchor_checks[0]['d']) == '2026-07-15'
        # 不得写/更新快照、不得写流水
        updates = [p for s, p in calls if 'INSERT INTO inventory_snapshot' in s]
        assert not updates, '手动锚点应被跳过，不得写快照'
        logs = [p for s, p in calls if 'INSERT INTO inventory_log' in s]
        assert not logs, '手动锚点应被跳过，不得写流水'


# ===========================================================================
# _daily_adjust  排除「回退日报」伪影日志（8/2 虚增 bug 回归测试）
# ===========================================================================

class TestDailyAdjustExcludesRollbackLogs:
    """回归测试：_daily_adjust 必须排除 reference LIKE '回退日报%' 的流水。
    此类流水是重新生成日报时撤销旧扣减的对销记录，若计入会虚增当天快照（8/2 bug 根因）。"""

    def test_sql_excludes_rollback_prefix(self, mock_conn):
        captured = []

        def side_effect(sql, params=None, **kw):
            captured.append((str(sql), params or {}))
            res = MagicMock()
            res.scalar.return_value = 0
            return res

        mock_conn.execute.side_effect = side_effect
        val = _daily_adjust(mock_conn, '花型A', '2026-08-02')
        assert val == 0.0
        sql, params = captured[0]
        assert "change_type = '手动调整'" in sql
        assert 'COALESCE(effect_date, DATE(created_at)) = :d' in sql
        assert "NOT LIKE '回退日报%'" in sql
        assert params.get('d') == '2026-08-02'

    def test_exclude_ref_prefix_adds_second_clause(self, mock_conn):
        captured = []

        def side_effect(sql, params=None, **kw):
            captured.append((str(sql), params or {}))
            res = MagicMock()
            res.scalar.return_value = 0
            return res

        mock_conn.execute.side_effect = side_effect
        _daily_adjust(mock_conn, '花型A', '2026-08-02', exclude_ref_prefix='本次调整')
        sql, params = captured[0]
        assert "NOT LIKE '回退日报%'" in sql
        assert 'NOT LIKE :excl' in sql
        assert params.get('excl') == '本次调整%'
# ===========================================================================
# update_inventory_snapshot
# ===========================================================================

class TestUpdateInventorySnapshot:

    def test_update_success(self, mock_conn):
        """Change from 100 to 150 should set anchor and recalculate to latest."""
        from datetime import date
        captured = []

        def side_effect(sql, params=None, **kw):
            if 'INSERT INTO inventory_snapshot' in str(sql) and 'ON DUPLICATE KEY' in str(sql):
                captured.append(params['d'])
            return make_side_effect(
                old_stock=100.0, latest=date(2026, 7, 20), sync_stock=150.0,
            )(sql, params, **kw)

        mock_conn.execute.side_effect = side_effect

        ok, msg, affected = update_inventory_snapshot(
            '花型A', '2026-07-15', 150, operator='tester', reason='盘点调整'
        )
        assert ok is True
        # 无后续锚点，重算 (7/15, 7/20] = 5 天
        assert affected == 5

    def test_update_success_stops_at_next_anchor(self, mock_conn):
        """目标日期之后存在锚点时，只重算到锚点前一天，锚点保留不重算。"""
        from datetime import date
        next_anchor_date = date(2026, 7, 20)
        captured = []

        def side_effect(sql, params=None, **kw):
            if 'INSERT INTO inventory_snapshot' in str(sql) and 'ON DUPLICATE KEY' in str(sql):
                captured.append(params['d'])
            return make_side_effect(
                old_stock=100.0, next_anchor=next_anchor_date, sync_stock=150.0,
            )(sql, params, **kw)

        mock_conn.execute.side_effect = side_effect

        ok, msg, affected = update_inventory_snapshot(
            '花型A', '2026-07-15', 150, operator='tester', reason='盘点调整'
        )
        assert ok is True
        # 重算区间 = (7/15, 7/19]，锚点 7/20 保留
        assert affected == 4
        assert date(2026, 7, 16) in captured
        assert date(2026, 7, 19) in captured
        assert date(2026, 7, 20) not in captured

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
# sync_inventory_current_stock
# ===========================================================================

class TestSyncInventoryCurrentStock:

    def test_sync_single_flower(self, mock_conn):
        """指定花型时，将 current_stock 同步为最新快照值。"""
        def side_effect(sql, params=None, **kw):
            res = MagicMock()
            if 'ORDER BY snapshot_date DESC LIMIT 1' in str(sql):
                res.fetchone.return_value = (120.0,)
            else:
                res.rowcount = 1
            return res

        mock_conn.execute.side_effect = side_effect
        sync_inventory_current_stock('花型A')
        ups = [str(c.args[0]) for c in mock_conn.execute.call_args_list
               if 'UPDATE inventory' in str(c.args[0])]
        assert any('current_stock' in s for s in ups)

    def test_sync_no_snapshot(self, mock_conn):
        """花型没有快照时，不应更新 inventory。"""
        mock_conn.execute.return_value.fetchone.return_value = None
        sync_inventory_current_stock('花型A')
        ups = [str(c.args[0]) for c in mock_conn.execute.call_args_list
               if 'UPDATE inventory' in str(c.args[0])]
        assert ups == []


# ===========================================================================
# rollback_inventory_to_date  (锚点感知)
# ===========================================================================

class TestRollbackInventoryToDate:

    def test_rollback_at_anchor(self, mock_conn):
        """目标是锚点：删除该日期之后所有快照，保留锚点本身。"""
        def side_effect(sql, params=None, **kw):
            s = str(sql)
            res = MagicMock()
            if 'snapshot_date = :d' in s and 'COUNT(*)' in s and 'is_manual' not in s:
                res.scalar.return_value = 1        # 目标日期有快照
            elif 'snapshot_date = :d' in s and 'is_manual = 1' in s:
                res.scalar.return_value = 1        # 目标是锚点
            elif 'COUNT(*)' in s and 'snapshot_date > :d' in s:
                res.scalar.return_value = 5
            elif 'DELETE' in s:
                res.rowcount = 5
            else:
                res.rowcount = 1
            return res

        mock_conn.execute.side_effect = side_effect
        ok, msg, count = rollback_inventory_to_date('2026-07-15')
        assert ok is True
        assert count == 5

    def test_rollback_not_anchor(self, mock_conn):
        """目标不是锚点且之后有锚点：只删除目标+1 .. 下一个锚点-1。"""
        from datetime import date
        delete_params = {}

        def side_effect(sql, params=None, **kw):
            s = str(sql)
            res = MagicMock()
            if 'snapshot_date = :d' in s and 'COUNT(*)' in s and 'is_manual' not in s:
                res.scalar.return_value = 1        # 目标日期有快照
            elif 'snapshot_date = :d' in s and 'is_manual = 1' in s:
                res.scalar.return_value = 0        # 目标不是锚点
            elif 'MIN(snapshot_date)' in s:
                res.scalar.return_value = date(2026, 7, 20)  # 下一个锚点
            elif 'COUNT(*)' in s and 'snapshot_date <= :end' in s:
                res.scalar.return_value = 4
            elif 'DELETE' in s:
                delete_params['params'] = params
                res.rowcount = 4
            else:
                res.rowcount = 1
            return res

        mock_conn.execute.side_effect = side_effect
        ok, msg, count = rollback_inventory_to_date('2026-07-15')
        assert ok is True
        assert count == 4
        # DELETE 应带上 end = 下一个锚点前一天（07-19）
        assert delete_params['params']['end'] == date(2026, 7, 19)


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
