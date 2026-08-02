# inventory_service.py
import pandas as pd
from sqlalchemy import text
from mysql_conn import engine
from datetime import datetime, timedelta
from system_service import get_system_start_date


# =====streamlit run app.py========================================
# 辅助函数：获取花型当前库存
# =============================================
def get_current_stock(flower):
    """查询单个花型的当前库存"""
    with engine.connect() as conn:
        result = conn.execute(
            text("SELECT current_stock FROM inventory WHERE flower = :f"),
            {"f": flower}
        ).fetchone()
        if result:
            return float(result[0])
        else:
            raise ValueError(f"花型 '{flower}' 不存在于库存表中")


# =============================================
# 锚点（手动调整快照）辅助函数
# is_manual=1 的快照为"锚点"：该日期库存值固定。
# 任何早于锚点的变动都只能影响锚点之前的日期，不能越过锚点；
# 晚于锚点的变动只影响锚点之后到下一个锚点之前的区间。
# =============================================

def _normalize_date(value):
    """将 str/date/datetime 统一转换为 date。"""
    if value is None:
        return datetime.now().date()
    if isinstance(value, str):
        try:
            return datetime.strptime(value, '%Y-%m-%d').date()
        except ValueError:
            return datetime.strptime(value, '%Y%m%d').date()
    if hasattr(value, 'date'):
        return value.date()
    return value


def _get_next_anchor(conn, flower, after_date):
    """返回 after_date 之后（不含）最近的锚点日期；无则返回 None。"""
    row = conn.execute(
        text("""
            SELECT snapshot_date FROM inventory_snapshot
            WHERE flower = :f AND is_manual = 1 AND snapshot_date > :d
            ORDER BY snapshot_date LIMIT 1
        """),
        {"f": flower, "d": after_date}
    ).fetchone()
    return row[0] if row else None


def _get_latest_snapshot_date_for(conn, flower):
    """返回该花型最新的快照日期；无则返回 None。"""
    return conn.execute(
        text("SELECT MAX(snapshot_date) FROM inventory_snapshot WHERE flower = :f"),
        {"f": flower}
    ).scalar()


def _daily_sales(conn, flower, d):
    """当日销售米数（来自日报缓存）。"""
    return float(conn.execute(
        text("SELECT COALESCE(SUM(total_meters), 0) FROM daily_report_cache WHERE report_date = :d AND flower = :f"),
        {"f": flower, "d": d}
    ).scalar() or 0)


def _daily_inbound(conn, flower, d):
    """当日入库数量（来自库存流水）。按生效日期 effect_date 归集；历史行无 effect_date 时回退到 created_at 的日期。"""
    return float(conn.execute(
        text("SELECT COALESCE(SUM(change_qty), 0) FROM inventory_log WHERE flower = :f AND change_type = '入库' AND COALESCE(effect_date, DATE(created_at)) = :d"),
        {"f": flower, "d": d}
    ).scalar() or 0)


def _daily_adjust(conn, flower, d, exclude_ref_prefix=None):
    """当日手动调整合计。按生效日期 effect_date 归集。exclude_ref_prefix 用于排除本次调整自身写入的流水。
    始终排除「回退日报」系统日志——那是重新生成日报时撤销旧扣减产生的对销记录，
    其正向 change_qty 与当天重新扣减的销售出库相抵，若计入会虚增当天快照。"""
    sql = ("SELECT COALESCE(SUM(change_qty), 0) FROM inventory_log "
           "WHERE flower = :f AND change_type = '手动调整' AND COALESCE(effect_date, DATE(created_at)) = :d"
           " AND (reference NOT LIKE '回退日报%' OR reference IS NULL)")
    params = {"f": flower, "d": d}
    if exclude_ref_prefix:
        sql += " AND (reference NOT LIKE :excl OR reference IS NULL)"
        params["excl"] = f"{exclude_ref_prefix}%"
    return float(conn.execute(text(sql), params).scalar() or 0)


def _ensure_snapshot_stock(conn, flower, target_date, operator):
    """
    确保 target_date 有快照；无则从最近的快照/锚点/基准逐日推算并插入。
    推算过程中遇到已有快照（锚点）以其值为准，不覆盖。
    返回该日期当前的库存值（变动前基准）。
    """
    prev_row = conn.execute(
        text("""
            SELECT stock, snapshot_date FROM inventory_snapshot
            WHERE flower = :f AND snapshot_date < :d
            ORDER BY snapshot_date DESC LIMIT 1
        """),
        {"f": flower, "d": target_date}
    ).fetchone()
    if prev_row:
        base_stock = float(prev_row[0])
        base_date = prev_row[1]
    else:
        base_row = conn.execute(
            text("SELECT base_stock, base_date FROM inventory_base WHERE flower = :f"),
            {"f": flower}
        ).fetchone()
        if base_row:
            base_stock = float(base_row[0])
            base_date = base_row[1]
        else:
            base_stock = get_current_stock(flower)
            base_date = get_system_start_date()

    walk_stock = base_stock
    walk_date = base_date + timedelta(days=1)
    while walk_date <= target_date:
        snap = conn.execute(
            text("SELECT stock FROM inventory_snapshot WHERE flower = :f AND snapshot_date = :d"),
            {"f": flower, "d": walk_date}
        ).fetchone()
        if snap is not None:
            walk_stock = float(snap[0])
        else:
            walk_stock = max(
                walk_stock - _daily_sales(conn, flower, walk_date)
                + _daily_inbound(conn, flower, walk_date)
                + _daily_adjust(conn, flower, walk_date),
                0
            )
        walk_date += timedelta(days=1)

    conn.execute(
        text("""
            INSERT INTO inventory_snapshot (flower, snapshot_date, stock, is_manual, updated_by, updated_at)
            VALUES (:f, :d, :s, 0, :op, CURRENT_TIMESTAMP)
        """),
        {"f": flower, "d": target_date, "s": walk_stock, "op": operator}
    )
    return walk_stock


def _recompute_segment(conn, flower, start_date, start_stock, end_date, operator, exclude_ref_prefix=None):
    """
    逐日重算 (start_date, end_date] 区间的快照（end_date 含，可为 None 表示无区间）。
    start_date 的库存已假定正确。遇到锚点即停止（锚点不可越过）。
    返回受影响天数。
    """
    if end_date is None or end_date <= start_date:
        return 0
    current_stock = start_stock
    affected = 0
    d = start_date
    while d < end_date:
        d += timedelta(days=1)
        anchor = conn.execute(
            text("SELECT stock FROM inventory_snapshot WHERE flower = :f AND snapshot_date = :d AND is_manual = 1"),
            {"f": flower, "d": d}
        ).fetchone()
        if anchor is not None:
            break
        current_stock = max(
            current_stock - _daily_sales(conn, flower, d)
            + _daily_inbound(conn, flower, d)
            + _daily_adjust(conn, flower, d, exclude_ref_prefix),
            0
        )
        conn.execute(
            text("""
                INSERT INTO inventory_snapshot (flower, snapshot_date, stock, is_manual, updated_by, updated_at)
                VALUES (:f, :d, :s, 0, :op, CURRENT_TIMESTAMP)
                ON DUPLICATE KEY UPDATE stock = :s2, updated_by = :op2, updated_at = CURRENT_TIMESTAMP
            """),
            {"f": flower, "d": d, "s": current_stock, "op": operator,
             "s2": current_stock, "op2": operator}
        )
        affected += 1
    return affected


def _write_log(conn, flower, change_type, qty, before, after, reference, operator, effect_date=None):
    """写入一条库存流水。effect_date 为该操作实际生效的库存日期（补录历史时与 created_at 不同）。"""
    conn.execute(
        text("""
            INSERT INTO inventory_log
            (flower, change_type, change_qty, before_stock, after_stock, reference, operator, effect_date)
            VALUES (:f, :ct, :q, :b, :a, :ref, :op, :eff)
        """),
        {"f": flower, "ct": change_type, "q": qty, "b": before, "a": after,
         "ref": reference, "op": operator, "eff": effect_date}
    )


def _sync_current_stock(conn, flower):
    """在给定连接内，将该花型 current_stock 同步为最新快照值。"""
    row = conn.execute(
        text("SELECT stock FROM inventory_snapshot WHERE flower = :f ORDER BY snapshot_date DESC LIMIT 1"),
        {"f": flower}
    ).fetchone()
    if row is not None:
        conn.execute(
            text("UPDATE inventory SET current_stock = :s WHERE flower = :f"),
            {"s": float(row[0]), "f": flower}
        )


def _sync_all_current_stock(conn):
    """在给定连接内，全量同步 inventory.current_stock = 各花型最新快照值。"""
    conn.execute(
        text("""
            UPDATE inventory inv
            JOIN (
                SELECT s.flower, s.stock
                FROM inventory_snapshot s
                JOIN (
                    SELECT flower, MAX(snapshot_date) AS maxd
                    FROM inventory_snapshot GROUP BY flower
                ) m ON s.flower = m.flower AND s.snapshot_date = m.maxd
            ) snap ON snap.flower = inv.flower
            SET inv.current_stock = snap.stock
        """)
    )


def sync_inventory_current_stock(flower=None):
    """
    同步 inventory.current_stock = 该花型最新日期快照的 stock。
    flower=None 时全量同步所有有快照的花型。
    """
    with engine.connect() as conn:
        if flower:
            _sync_current_stock(conn, flower)
        else:
            _sync_all_current_stock(conn)


# =============================================
# 核心功能 1：手动入库（增加库存）+ 自动补录缺口
# =============================================
def add_stock(flower, qty, reference="手动入库", operator="system", target_date=None):
    """
    增加库存（采购入库/手动补货）
    同时更新 inventory 表和 inventory_snapshot 表
    target_date: 入库生效日期。只影响该日期到下一个锚点（is_manual=1）之前的快照，
    不会越过锚点改变锚点之后日期的库存。默认今天。
    """
    if qty <= 0:
        raise ValueError("入库数量必须大于 0")

    active, msg = check_flower_active(flower)
    if not active:
        raise ValueError(msg)

    target_date = _normalize_date(target_date)

    with engine.connect() as conn:
        trans = conn.begin()
        try:
            # ── 第 0 步：读取 target_date 的旧快照值（作为变动前基准） ──
            old_row = conn.execute(
                text("SELECT stock FROM inventory_snapshot WHERE flower = :f AND snapshot_date = :d"),
                {"f": flower, "d": target_date}
            ).fetchone()

            if old_row is not None:
                old_snapshot_stock = float(old_row[0])
            else:
                # target_date 无快照 → 从最近快照/锚点/基准推算至此日并创建（不越过锚点）
                old_snapshot_stock = _ensure_snapshot_stock(conn, flower, target_date, operator)

            new_snapshot_stock = old_snapshot_stock + qty

            # ── 第 1 步：更新 target_date 快照 ──
            conn.execute(
                text("""
                    UPDATE inventory_snapshot
                    SET stock = :new, updated_by = :op, updated_at = CURRENT_TIMESTAMP
                    WHERE flower = :f AND snapshot_date = :d
                """),
                {"new": new_snapshot_stock, "op": operator, "f": flower, "d": target_date}
            )

            # ── 第 2 步：确定重算区间（target_date 之后到下一个锚点之前） ──
            next_anchor = _get_next_anchor(conn, flower, target_date)
            if next_anchor is not None:
                end_date = next_anchor - timedelta(days=1)
            else:
                end_date = _get_latest_snapshot_date_for(conn, flower)

            # ── 第 3 步：逐日重算后续快照（不越过锚点） ──
            affected = _recompute_segment(
                conn, flower, target_date, new_snapshot_stock, end_date, operator
            )

            # ── 第 4 步：写入库存流水（用快照值） ──
            _write_log(
                conn, flower, '入库', qty, old_snapshot_stock, new_snapshot_stock,
                reference, operator, effect_date=target_date
            )

            # ── 第 5 步：同步 current_stock = 最新快照 ──
            _sync_current_stock(conn, flower)

            trans.commit()
            print(f"✅ 入库成功：{flower} +{qty} 米（快照 {old_snapshot_stock}→{new_snapshot_stock}，生效 {target_date}，后续重算 {affected} 天）")

        except Exception as e:
            trans.rollback()
            raise e
# =============================================
# 核心功能 2：销售出库（减少库存）
# =============================================
def deduct_stock(flower, qty, reference="销售出库", operator="system", report_date=None, is_daily_sales=False):
    """
    手动出库：直接扣减库存，同时更新 inventory 和 inventory_snapshot
    report_date: 出库日期。只影响该日期到下一个锚点（is_manual=1）之前的快照，
    不会越过锚点改变锚点之后日期的库存。库存不会低于 0。
    is_daily_sales: 日报自动扣减路径（True 时 qty 即当日销售米数）。快照补全逻辑
    会把当天销售烘焙进 snapshot[report_date]（end-of-day 语义），若日报扣减再以该
    快照为基准减去 qty 会双重扣减。故以 snapshot[report_date-1] 为基准，结合当日
    出入库/调整自愈重算，保证日报可反复重新生成而不累积误差。
    """
    if qty <= 0:
        raise ValueError("出库数量必须大于 0")

    active, msg = check_flower_active(flower)
    if not active:
        raise ValueError(msg)

    report_date = _normalize_date(report_date)

    with engine.connect() as conn:
        trans = conn.begin()
        try:
            # 0. 日报路径：若 report_date 为手动锚点（is_manual=1），锚点是实盘数、
            #    已含当日销售，任何公式扣减都会破坏锚点，直接跳过
            if is_daily_sales:
                anchor_row = conn.execute(
                    text("SELECT is_manual FROM inventory_snapshot WHERE flower = :f AND snapshot_date = :d"),
                    {"f": flower, "d": report_date}
                ).fetchone()
                if anchor_row is not None and anchor_row[0]:
                    print(f"⏭️ {flower} {report_date} 为手动锚点（is_manual=1），日报扣减跳过，锚点为实盘数")
                    return

            # 1. 确保目标日期及之后有快照记录（沿用原逻辑，缺失时先补全）
            check_snapshot = conn.execute(
                text("SELECT COUNT(*) FROM inventory_snapshot WHERE flower = :f AND snapshot_date >= :d"),
                {"f": flower, "d": report_date}
            ).scalar()

            if check_snapshot == 0:
                print(f"⚠️ {flower} 从 {report_date} 起没有快照记录，正在补全...")
                from inventory_service import fill_missing_snapshots
                fill_missing_snapshots(operator=operator)

            # 2. 确定扣减基准
            if is_daily_sales:
                # 日报路径：snapshot[report_date] 可能已被补全逻辑烘焙成含当天销售的
                # end-of-day 值，不能直接作为基准。以 snapshot[report_date-1] 推算
                # 当日 end-of-day，实现自愈（可反复重新生成，不累积双重扣减）。
                prev_date = report_date - timedelta(days=1)
                prev_row = conn.execute(
                    text("SELECT stock FROM inventory_snapshot WHERE flower = :f AND snapshot_date = :d"),
                    {"f": flower, "d": prev_date}
                ).fetchone()
                if prev_row is not None:
                    snap_before = float(prev_row[0])
                else:
                    snap_before = _ensure_snapshot_stock(conn, flower, prev_date, operator)
                snap_after = max(
                    snap_before - qty
                    + _daily_inbound(conn, flower, report_date)
                    + _daily_adjust(conn, flower, report_date),
                    0
                )
                actual_deduct = qty
            else:
                # 手动路径：读取 report_date 快照旧值（无则推算创建），用于日志与扣减基准
                snap_before_row = conn.execute(
                    text("SELECT stock FROM inventory_snapshot WHERE flower = :f AND snapshot_date = :d"),
                    {"f": flower, "d": report_date}
                ).fetchone()
                if snap_before_row is not None:
                    snap_before = float(snap_before_row[0])
                else:
                    snap_before = _ensure_snapshot_stock(conn, flower, report_date, operator)
                snap_after = max(snap_before - qty, 0)
                actual_deduct = snap_before - snap_after

            # 3. 更新 report_date 快照（扣减，不低于 0；无则插入，保留 is_manual 原值）
            conn.execute(
                text("""
                    INSERT INTO inventory_snapshot (flower, snapshot_date, stock, is_manual, updated_by, updated_at)
                    VALUES (:f, :d, :new, 0, :op, CURRENT_TIMESTAMP)
                    ON DUPLICATE KEY UPDATE stock = :new2, updated_by = :op2, updated_at = CURRENT_TIMESTAMP
                """),
                {"f": flower, "d": report_date, "new": snap_after, "op": operator,
                 "new2": snap_after, "op2": operator}
            )

            # 4. 确定重算区间（report_date 之后到下一个锚点之前）
            next_anchor = _get_next_anchor(conn, flower, report_date)
            if next_anchor is not None:
                end_date = next_anchor - timedelta(days=1)
            else:
                end_date = _get_latest_snapshot_date_for(conn, flower)

            # 5. 逐日重算后续快照（不越过锚点）
            affected = _recompute_segment(
                conn, flower, report_date, snap_after, end_date, operator
            )

            # 6. 写入流水（用快照值）
            _write_log(
                conn, flower, '销售出库', -actual_deduct, snap_before, snap_after,
                reference, operator, effect_date=report_date
            )

            # 7. 同步 current_stock = 最新快照
            _sync_current_stock(conn, flower)

            trans.commit()
            print(f"🔍 快照表重算影响 {affected} 天（从 {report_date} 起到下一个锚点之前）")

            if snap_before < qty:
                print(f"⚠️ {flower} 库存不足：当前 {snap_before} 米，出库 {qty} 米，实际扣减 {actual_deduct} 米，库存保持为 0")
            else:
                print(f"✅ {flower} 扣减 {qty} 米（剩余：{snap_after}）")

        except Exception as e:
            trans.rollback()
            raise e
# =============================================
# 核心功能 3：回退某一天的销售出库（用于日报重新生成）
# =============================================
def rollback_daily_sales(target_date, operator="system"):
    """
    回退指定日期的所有销售出库（撤销日报扣减的库存）
    返回：(回退记录数, 是否成功, 消息)
    """
    with engine.connect() as conn:
        trans = conn.begin()
        try:
            # 查询该日期所有销售出库记录（优先用 reference 模糊匹配）
            logs = conn.execute(
                text("""
                    SELECT id, flower, change_qty, before_stock, after_stock
                    FROM inventory_log
                    WHERE change_type = '销售出库'
                      AND reference LIKE :ref_pattern
                """),
                {"ref_pattern": f"%{target_date}%"}
            ).fetchall()

            # 如果没找到，再用日期匹配
            if not logs:
                logs = conn.execute(
                    text("""
                        SELECT id, flower, change_qty, before_stock, after_stock
                        FROM inventory_log
                        WHERE change_type = '销售出库'
                          AND DATE(created_at) = :d
                    """),
                    {"d": target_date}
                ).fetchall()

            if not logs:
                trans.commit()
                return (0, True, f"ℹ️ 未找到 {target_date} 的销售出库记录")  # 统一返回三个值

            # 回退库存
            for log in logs:
                log_id = log[0]
                flower = log[1]
                change_qty = float(log[2])  # 负数
                before_stock = float(log[3])
                after_stock = float(log[4])

                current = get_current_stock(flower)
                target_stock = current - change_qty  # 减去负数 = 加回来

                conn.execute(
                    text("UPDATE inventory SET current_stock = :new WHERE flower = :f"),
                    {"new": target_stock, "f": flower}
                )

                # ★ 修复：同步回退 inventory_snapshot（change_qty 为负数，减负数=加回）
                result_snap = conn.execute(
                    text("""
                        UPDATE inventory_snapshot
                        SET stock = GREATEST(stock - :qty, 0),
                            updated_by = :op,
                            updated_at = CURRENT_TIMESTAMP
                        WHERE flower = :f AND snapshot_date >= :d
                    """),
                    {"qty": change_qty, "op": operator, "f": flower, "d": target_date}
                )
                print(f"🔍 快照表回退影响行数：{result_snap.rowcount}（从 {target_date} 起，花型 {flower}）")

                conn.execute(
                    text("""
                        INSERT INTO inventory_log
                        (flower, change_type, change_qty, before_stock, after_stock, reference, operator, effect_date)
                        VALUES (:f, '手动调整', :qty, :before, :after, :ref, :op, :eff)
                    """),
                    {
                        "f": flower,
                        "qty": -change_qty,
                        "before": current,
                        "after": target_stock,
                        "ref": f"回退日报 {target_date}（重新生成前）",
                        "op": operator,
                        "eff": target_date
                    }
                )

            # 删除该日期的缺口记录（如果有）
            conn.execute(
                text("DELETE FROM inventory_shortfall WHERE reference_date = :d"),
                {"d": target_date}
            )

            trans.commit()
            return (len(logs), True, f"✅ 成功回退 {len(logs)} 条销售出库记录")

        except Exception as e:
            trans.rollback()
            return (0, False, f"❌ 回退异常：{str(e)}")

# =============================================
# 核心功能 4：查看所有库存
# =============================================
def get_inventory_report():
    """获取最新一天的库存快照（即当前库存），返回 DataFrame 列：['花型', '当前库存(米)', '预警天数', '供应商交期(天)']"""
    latest_date = get_latest_snapshot_date()
    if not latest_date:
        return pd.DataFrame(columns=['花型', '当前库存(米)', '预警天数', '供应商交期(天)'])
    df = get_inventory_snapshot(latest_date)
    if df.empty:
        return pd.DataFrame(columns=['花型', '当前库存(米)', '预警天数', '供应商交期(天)'])
    # 重命名列
    df = df.rename(columns={'库存': '当前库存(米)'})  # 此时列名已是 '花型' 和 '当前库存(米)'
    with engine.connect() as conn:
        alert_df = pd.read_sql(
            text("SELECT flower, alert_days, supplier_lead_time FROM inventory"),
            conn
        )
    # 将 alert_df 的 flower 列改为 '花型' 以便合并
    alert_df = alert_df.rename(columns={'flower': '花型'})
    df = df.merge(alert_df, on='花型', how='left')
    df['预警天数'] = df['alert_days'].fillna(7).astype(int)
    df['供应商交期(天)'] = df['supplier_lead_time'].fillna(3).astype(int)
    return df[['花型', '当前库存(米)', '预警天数', '供应商交期(天)']]

# =============================================
# 核心功能 5：查看库存流水
# =============================================
def get_stock_log(flower=None, days=30):
    """查看库存变动流水"""
    with engine.connect() as conn:
        if flower:
            query = text("""
                SELECT
                    COALESCE(effect_date, DATE(created_at)) AS 日期,
                    change_type AS 变动类型,
                    change_qty AS 变动数量,
                    before_stock AS 变动前,
                    after_stock AS 变动后,
                    reference AS 备注
                FROM inventory_log
                WHERE flower = :f AND created_at >= DATE_SUB(NOW(), INTERVAL :d DAY)
                ORDER BY created_at DESC
            """)
            df = pd.read_sql(query, conn, params={"f": flower, "d": days})
        else:
            query = text("""
                SELECT
                    flower AS 花型,
                    COALESCE(effect_date, DATE(created_at)) AS 日期,
                    change_type AS 变动类型,
                    change_qty AS 变动数量,
                    before_stock AS 变动前,
                    after_stock AS 变动后,
                    reference AS 备注
                FROM inventory_log
                WHERE created_at >= DATE_SUB(NOW(), INTERVAL :d DAY)
                ORDER BY created_at DESC
            """)
            df = pd.read_sql(query, conn, params={"d": days})
    return df


# =============================================
# 核心功能 6：报损登记
# =============================================
def write_off_stock(flower, qty, reason="报损", operator="system"):
    """报损（次品、裁剪损耗等）"""
    if qty <= 0:
        raise ValueError("报损数量必须大于 0")

    active, msg = check_flower_active(flower)
    if not active:
        raise ValueError(msg)
    with engine.connect() as conn:
        trans = conn.begin()
        try:
            current = get_current_stock(flower)
            if current < qty:
                raise ValueError(f"❌ {flower} 库存不足，无法报损 {qty} 米（当前仅 {current} 米）")
            new_stock = current - qty
            conn.execute(
                text("UPDATE inventory SET current_stock = :new WHERE flower = :f"),
                {"new": new_stock, "f": flower}
            )
            conn.execute(
                text("""
                    INSERT INTO inventory_log
                    (flower, change_type, change_qty, before_stock, after_stock, reference, operator, effect_date)
                    VALUES (:f, '报损', :qty, :before, :after, :ref, :op, :eff)
                """),
                {"f": flower, "qty": -qty, "before": current, "after": new_stock,
                 "ref": reason, "op": operator, "eff": datetime.now().date()}
            )
            trans.commit()
            print(f"✅ 报损成功：{flower} 损耗 {qty} 米（剩余库存：{new_stock}）")
        except Exception as e:
            trans.rollback()
            raise e


# =============================================
# 核心功能 7：回退库存到指定日期
# =============================================
def rollback_inventory_to_date(target_date, operator="system"):
    """
    整体回退库存到指定日期。
    - 若 target_date 是锚点（is_manual=1）：删除该日期之后的所有快照（保留锚点本身）。
    - 若 target_date 不是锚点：保留所有锚点，只删除从 target_date+1 到下一个锚点-1 的快照。
    返回：(成功/失败, 消息, 受影响花型数)
    """
    from datetime import datetime
    try:
        target_dt = datetime.strptime(target_date, "%Y-%m-%d")
    except ValueError:
        return (False, "日期格式错误，请使用 YYYY-MM-DD 格式", 0)

    with engine.connect() as conn:
        trans = conn.begin()
        try:
            # 1. 检查目标日期的快照是否存在
            check = conn.execute(
                text("SELECT COUNT(*) FROM inventory_snapshot WHERE snapshot_date = :d"),
                {"d": target_date}
            ).scalar()
            if check == 0:
                trans.rollback()
                return (False, f"❌ {target_date} 没有快照数据，无法回退", 0)

            # 2. 判断 target_date 是否为锚点，决定删除范围
            is_anchor = conn.execute(
                text("SELECT COUNT(*) FROM inventory_snapshot WHERE snapshot_date = :d AND is_manual = 1"),
                {"d": target_date}
            ).scalar()

            if is_anchor > 0:
                # 锚点：删除该日期之后的所有快照（保留锚点本身）
                del_sql = "DELETE FROM inventory_snapshot WHERE snapshot_date > :d"
                del_params = {"d": target_date}
                to_delete = conn.execute(
                    text("SELECT COUNT(*) FROM inventory_snapshot WHERE snapshot_date > :d"),
                    {"d": target_date}
                ).scalar()
            else:
                # 非锚点：保留所有锚点，只删除 target_date+1 .. 下一个锚点-1
                next_anchor = conn.execute(
                    text("SELECT MIN(snapshot_date) FROM inventory_snapshot WHERE snapshot_date > :d AND is_manual = 1"),
                    {"d": target_date}
                ).scalar()
                if next_anchor is not None:
                    end_date = next_anchor - timedelta(days=1)
                    del_sql = "DELETE FROM inventory_snapshot WHERE snapshot_date > :d AND snapshot_date <= :end"
                    del_params = {"d": target_date, "end": end_date}
                    to_delete = conn.execute(
                        text("SELECT COUNT(*) FROM inventory_snapshot WHERE snapshot_date > :d AND snapshot_date <= :end"),
                        {"d": target_date, "end": end_date}
                    ).scalar()
                else:
                    # 无锚点 → 删除 target_date 之后所有快照
                    del_sql = "DELETE FROM inventory_snapshot WHERE snapshot_date > :d"
                    del_params = {"d": target_date}
                    to_delete = conn.execute(
                        text("SELECT COUNT(*) FROM inventory_snapshot WHERE snapshot_date > :d"),
                        {"d": target_date}
                    ).scalar()

            if to_delete == 0:
                trans.rollback()
                return (False, f"ℹ️ {target_date} 之后没有快照数据，无需回退", 0)

            # 3. 记录回退操作到库存流水
            conn.execute(
                text("""
                    INSERT INTO inventory_log
                    (flower, change_type, change_qty, before_stock, after_stock, reference, operator, effect_date)
                    VALUES ('系统', '手动调整', 0, 0, 0, :ref, :op, :eff)
                """),
                {
                    "ref": f"回退库存至 {target_date}，删除 {to_delete} 条快照记录",
                    "op": operator,
                    "eff": target_date
                }
            )

            # 4. 删除快照
            deleted = conn.execute(text(del_sql), del_params).rowcount

            # 5. 同步 current_stock = 各花型最新快照
            _sync_all_current_stock(conn)

            trans.commit()
            return (True, f"✅ 已回退到 {target_date}，共删除 {deleted} 条快照记录", deleted)

        except Exception as e:
            trans.rollback()
            return (False, f"❌ 回退失败：{str(e)}", 0)
# =============================================
# 核心功能：手动调整库存（直接设置为目标值）
# =============================================
def adjust_stock(flower, target_stock, reference="手动调整", operator="system"):
    """
    直接将某个花型的库存调整为指定值（正数）
    自动计算差额并写入流水
    """
    if target_stock < 0:
        raise ValueError("目标库存不能为负数")
    if not flower:
        raise ValueError("花型名称不能为空")

    active, msg = check_flower_active(flower)
    if not active:
        raise ValueError(msg)

    with engine.connect() as conn:
        trans = conn.begin()
        try:
            # 1. 检查花型是否存在
            current = get_current_stock(flower)

            # 2. 计算差额
            diff = target_stock - current  # 正=需要增加，负=需要减少

            if abs(diff) < 0.001:
                # 没有变化，直接返回
                trans.commit()
                return (current, current, 0, "库存无变化")

            # 3. 更新库存
            conn.execute(
                text("UPDATE inventory SET current_stock = :new WHERE flower = :f"),
                {"new": target_stock, "f": flower}
            )

            # 4. 写入流水
            conn.execute(
                text("""
                    INSERT INTO inventory_log
                    (flower, change_type, change_qty, before_stock, after_stock, reference, operator, effect_date)
                    VALUES (:f, '手动调整', :qty, :before, :after, :ref, :op, :eff)
                """),
                {
                    "f": flower,
                    "qty": diff,
                    "before": current,
                    "after": target_stock,
                    "ref": reference,
                    "op": operator,
                    "eff": datetime.now().date()
                }
            )

            trans.commit()
            return (current, target_stock, diff,
                    f"✅ {flower} 库存从 {current} 调整为 {target_stock}（{'增加' if diff > 0 else '减少'} {abs(diff)} 米）")

        except Exception as e:
            trans.rollback()
            raise e


# ============================================================
# 新增：库存快照管理
# ============================================================

def init_inventory_base(base_date='2026-07-01', base_stock=200, operator='system'):
    """
    初始化库存基准（将所有花型的基准库存设为 base_stock）
    并在基准日期生成快照
    """
    with engine.connect() as conn:
        trans = conn.begin()
        try:
            # 1. 获取所有花型
            flowers = conn.execute(text("SELECT flower FROM product_cost")).fetchall()
            if not flowers:
                return (False, "product_cost 表为空，请先导入花型")

            # 2. 清空基准表和快照表（谨慎）
            conn.execute(text("DELETE FROM inventory_base"))
            conn.execute(text("DELETE FROM inventory_snapshot"))

            # 3. 插入基准数据
            for (flower,) in flowers:
                conn.execute(
                    text("""
                        INSERT INTO inventory_base (flower, base_stock, base_date)
                        VALUES (:f, :stock, :date)
                    """),
                    {"f": flower, "stock": base_stock, "date": base_date}
                )

            # 4. 生成基准日期的快照
            for (flower,) in flowers:
                conn.execute(
                    text("""
                        INSERT INTO inventory_snapshot (flower, snapshot_date, stock, updated_by)
                        VALUES (:f, :date, :stock, :op)
                    """),
                    {"f": flower, "date": base_date, "stock": base_stock, "op": operator}
                )

            conn.commit()
            return (True, f"✅ 基准库存初始化完成：{len(flowers)} 个花型，基准日期 {base_date}，库存 {base_stock} 米")
        except Exception as e:
            trans.rollback()
            return (False, f"❌ 初始化失败：{str(e)}")


def get_latest_snapshot_date():
    """获取最新库存快照日期（所有花型中最新的日期）"""
    with engine.connect() as conn:
        result = conn.execute(
            text("SELECT MAX(snapshot_date) FROM inventory_snapshot")
        ).scalar()
        return result


def get_missing_snapshot_dates(up_to_date=None):
    """
    获取缺失快照的日期（从基准日期到 up_to_date）
    返回缺失日期的列表
    """
    if up_to_date is None:
        up_to_date = datetime.now().date()

    with engine.connect() as conn:
        # 获取基准日期
        base = conn.execute(
            text("SELECT MIN(base_date) FROM inventory_base")
        ).scalar()
        if not base:
            return []

        # 获取已有快照的日期
        existing = conn.execute(
            text("SELECT DISTINCT snapshot_date FROM inventory_snapshot WHERE snapshot_date BETWEEN :start AND :end"),
            {"start": base, "end": up_to_date}
        ).fetchall()
        existing_dates = {row[0] for row in existing}

        # 生成所有日期
        all_dates = []
        current = base
        while current <= up_to_date:
            all_dates.append(current)
            current += timedelta(days=1)

        # 缺失的日期
        missing = [d for d in all_dates if d not in existing_dates]
        return missing


def get_inventory_snapshot(target_date, flower=None):
    """查询指定日期的库存快照，返回 DataFrame 列名：['花型', '库存']"""
    with engine.connect() as conn:
        if flower:
            result = conn.execute(
                text("SELECT stock FROM inventory_snapshot WHERE flower = :f AND snapshot_date = :d"),
                {"f": flower, "d": target_date}
            ).fetchone()
            if result:
                return pd.DataFrame([{'花型': flower, '库存': float(result[0])}])
            else:
                return pd.DataFrame(columns=['花型', '库存'])
        else:
            df = pd.read_sql(
                text("SELECT flower, stock FROM inventory_snapshot WHERE snapshot_date = :d ORDER BY flower"),
                conn,
                params={"d": target_date}
            )
            if df.empty:
                return pd.DataFrame(columns=['花型', '库存'])
            df = df.rename(columns={'flower': '花型', 'stock': '库存'})
            return df


def fill_missing_snapshots(up_to_date=None, operator='system'):
    """
    补全缺失的库存快照。
    🔧 不依赖日报：即使没有日报数据，也会自动把快照延伸到今天，
    每天库存沿用前一天的库存（日报销售=0）。
    返回: (补全天数, 状态码, 消息)
    """
    from system_service import get_system_start_date

    start_date = get_system_start_date()
    print(f"🔍 [DEBUG] 系统起始日期: {start_date}")

    with engine.connect() as conn:
        # 检查 inventory_base
        base_check = conn.execute(
            text("SELECT COUNT(*) FROM inventory_base")
        ).scalar()
        print(f"🔍 [DEBUG] inventory_base 记录数: {base_check}")
        if base_check == 0:
            return (0, "error", "❌ inventory_base 表为空，请先初始化基准库存")

        # 🔧 补全范围：默认到 today，不强制依赖日报
        today = datetime.now().date()
        if up_to_date is None:
            up_to_date = today
        elif isinstance(up_to_date, str):
            up_to_date = datetime.strptime(up_to_date, '%Y-%m-%d').date()
        # 不能超过今天
        if up_to_date > today:
            up_to_date = today
        print(f"🔍 [DEBUG] 目标补全日期: {up_to_date}")

        # 获取所有花型（从 product_cost，因为 inventory_base 可能缺新花型）
        flowers_raw = conn.execute(
            text("SELECT flower FROM product_cost WHERE is_deleted = 0")
        ).fetchall()
        if not flowers_raw:
            return (0, "error", "❌ product_cost 表中没有花型数据")
        flowers = [f[0] for f in flowers_raw]
        print(f"🔍 [DEBUG] 花型数量: {len(flowers)}")

        # 确保所有花型在 inventory_base 中有记录
        for flower in flowers:
            exists = conn.execute(
                text("SELECT COUNT(*) FROM inventory_base WHERE flower = :f"),
                {"f": flower}
            ).scalar()
            if exists == 0:
                conn.execute(
                    text("INSERT INTO inventory_base (flower, base_stock, base_date) VALUES (:f, 0, :d)"),
                    {"f": flower, "d": start_date}
                )

        # 获取已有快照的最新日期
        latest = conn.execute(
            text("SELECT MAX(snapshot_date) FROM inventory_snapshot WHERE snapshot_date >= :start"),
            {"start": start_date}
        ).scalar()
        print(f"🔍 [DEBUG] 已有快照最新日期: {latest}")

        if latest:
            start_date_compute = latest + timedelta(days=1)
        else:
            start_date_compute = start_date
        print(f"🔍 [DEBUG] 开始补全日期: {start_date_compute}")

        if start_date_compute > up_to_date:
            return (0, "already_latest", f"✅ 库存快照已是最新（截至 {up_to_date.strftime('%Y-%m-%d')}）")

        # 执行补全：逐天计算快照。锚点（is_manual=1）作为分段的固定值，不可跨越。
        filled_count = 0
        current = start_date_compute

        # 初始前一天库存：优先用 start_date_compute 前一天的快照，否则用基准库存
        prev_date = current - timedelta(days=1)
        if prev_date >= start_date:
            prev_df = pd.read_sql(
                text("SELECT flower, stock FROM inventory_snapshot WHERE snapshot_date = :d"),
                conn,
                params={"d": prev_date}
            )
        else:
            prev_df = pd.DataFrame()
        if prev_df.empty:
            prev_df = pd.read_sql(
                text("SELECT flower, base_stock as stock FROM inventory_base"),
                conn
            )
        prev_stock = dict(zip(prev_df['flower'], prev_df['stock']))

        while current <= up_to_date:
            # 获取当天的日报销售数据（有则扣减，无则为0，不影响快照延续）
            sales_df = pd.read_sql(
                text("SELECT flower, total_meters FROM daily_report_cache WHERE report_date = :d"),
                conn,
                params={"d": current}
            )
            sales = dict(zip(sales_df['flower'], sales_df['total_meters']))

            # 计算当天快照
            for flower in flowers:
                # 该日已有快照（通常是锚点 is_manual=1）：以其值为准，作为后续基准，不覆盖
                existing = conn.execute(
                    text("SELECT stock FROM inventory_snapshot WHERE flower = :f AND snapshot_date = :d"),
                    {"f": flower, "d": current}
                ).fetchone()
                if existing is not None:
                    prev_stock[flower] = float(existing[0])
                    continue

                stock = prev_stock.get(flower, 0) - sales.get(flower, 0)
                if stock < 0:
                    stock = 0

                conn.execute(
                    text("""
                        INSERT IGNORE INTO inventory_snapshot (flower, snapshot_date, stock, is_manual, updated_by)
                        VALUES (:f, :d, :stock, 0, :op)
                    """),
                    {"f": flower, "d": current, "stock": stock, "op": operator}
                )
                prev_stock[flower] = stock

            filled_count += 1
            print(f"  ✅ {current} 快照已生成")
            current += timedelta(days=1)
        conn.commit()
        print(f"✅ 已补全 {filled_count} 天的快照（截至 {up_to_date.strftime('%Y-%m-%d')}）")
        return (filled_count, "success", f"✅ 已补全 {filled_count} 天的快照（截至 {up_to_date.strftime('%Y-%m-%d')}）")


def update_inventory_snapshot(flower, target_date, new_stock, operator='system', reason='手动调整'):
    """
    手动调整某天花型库存（该日期成为锚点，is_manual=1，库存值固定），
    并联动重算该日期之后到下一个锚点之间的快照。
    早于锚点的变动不能越过锚点；晚于锚点的变动只影响锚点之后到下一个锚点之前。
    返回：(成功/失败, 消息, 受影响的天数)
    """
    target_date = _normalize_date(target_date)
    if new_stock < 0:
        return (False, "库存不能为负数", 0)

    with engine.connect() as conn:
        trans = conn.begin()
        try:
            # 1. 获取当前快照（旧值）
            old = conn.execute(
                text("SELECT stock FROM inventory_snapshot WHERE flower = :f AND snapshot_date = :d"),
                {"f": flower, "d": target_date}
            ).fetchone()
            if not old:
                return (False, f"未找到 {target_date} 的花型 {flower} 库存快照", 0)
            old_stock = float(old[0])
            delta = new_stock - old_stock
            if abs(delta) < 0.001:
                return (True, "库存无变化", 0)

            # 2. 设置目标日期快照为锚点（is_manual=1）
            conn.execute(
                text("""
                    UPDATE inventory_snapshot
                    SET stock = :new, is_manual = 1, updated_by = :op, updated_at = CURRENT_TIMESTAMP
                    WHERE flower = :f AND snapshot_date = :d
                """),
                {"new": new_stock, "op": operator, "f": flower, "d": target_date}
            )

            # 3. 查找目标日期之后第一个锚点，确定重算区间
            next_anchor = _get_next_anchor(conn, flower, target_date)
            if next_anchor is not None:
                end_date = next_anchor - timedelta(days=1)
            else:
                end_date = _get_latest_snapshot_date_for(conn, flower)

            # 4. 删除 target_date+1 .. end_date 之间的旧快照
            if end_date is not None and end_date > target_date:
                conn.execute(
                    text("""
                        DELETE FROM inventory_snapshot
                        WHERE flower = :f AND snapshot_date > :d AND snapshot_date <= :end
                    """),
                    {"f": flower, "d": target_date, "end": end_date}
                )

            # 5. 从 target_date+1 逐日重算（不越过锚点）
            affected = _recompute_segment(
                conn, flower, target_date, new_stock, end_date, operator,
                exclude_ref_prefix=f"快照调整 {target_date}"
            )

            # 6. 写入库存流水（reference 标记为快照调整，重算时排除自身）
            _write_log(
                conn, flower, '手动调整', delta, old_stock, new_stock,
                f"快照调整 {target_date}（{reason}）", operator, effect_date=target_date
            )

            # 7. 记录变更日志（inventory_change_log）
            conn.execute(
                text("""
                    INSERT INTO inventory_change_log
                    (flower, change_date, old_stock, new_stock, reason, operator)
                    VALUES (:f, :d, :old, :new, :reason, :op)
                """),
                {"f": flower, "d": target_date, "old": old_stock, "new": new_stock,
                 "reason": reason, "op": operator}
            )

            # 8. 同步 current_stock = 最新快照
            _sync_current_stock(conn, flower)

            trans.commit()
            return (True,
                    f"✅ {flower} 在 {target_date} 库存从 {old_stock} 调整为 {new_stock}"
                    f"（{'+' if delta > 0 else ''}{delta}），影响 {affected} 天",
                    affected)
        except Exception as e:
            trans.rollback()
            return (False, f"❌ 修改失败：{str(e)}", 0)

def get_inventory_change_log(flower=None, days=30):
    """查询库存变更日志"""
    with engine.connect() as conn:
        if flower:
            df = pd.read_sql(
                text("""
                    SELECT * FROM inventory_change_log
                    WHERE flower = :f AND changed_at >= DATE_SUB(NOW(), INTERVAL :d DAY)
                    ORDER BY changed_at DESC
                """),
                conn,
                params={"f": flower, "d": days}
            )
        else:
            df = pd.read_sql(
                text("""
                    SELECT * FROM inventory_change_log
                    WHERE changed_at >= DATE_SUB(NOW(), INTERVAL :d DAY)
                    ORDER BY changed_at DESC
                """),
                conn,
                params={"d": days}
            )
        return df


def get_system_status():
    """获取系统状态，快照可延伸到当天（不依赖日报）"""
    status = {}
    start_date = get_system_start_date()

    with engine.connect() as conn:
        # 最新库存日期
        latest_snapshot = conn.execute(
            text("SELECT MAX(snapshot_date) FROM inventory_snapshot WHERE snapshot_date >= :start"),
            {"start": start_date}
        ).scalar()
        status['latest_snapshot'] = latest_snapshot

        # 最新日报日期
        latest_report = conn.execute(
            text("SELECT MAX(report_date) FROM daily_report_cache WHERE report_date >= :start"),
            {"start": start_date}
        ).scalar()
        status['latest_report'] = latest_report

        # 🔧 缺失快照：从起始日期到 today（不再依赖日报日期）
        today = datetime.now().date()
        effective_date = latest_snapshot if latest_snapshot else today
        # 至少覆盖到 start_date
        end_date = today

        # 获取已有快照日期
        snapshot_dates = conn.execute(
            text(
                "SELECT DISTINCT snapshot_date FROM inventory_snapshot WHERE snapshot_date >= :start AND snapshot_date <= :end"),
            {"start": start_date, "end": end_date}
        ).fetchall()
        snapshot_dates_set = {row[0] for row in snapshot_dates if row[0] is not None}

        # 生成所有日期
        all_dates = []
        current = start_date
        while current <= end_date:
            all_dates.append(current)
            current += timedelta(days=1)

        missing_snapshots = [d for d in all_dates if d not in snapshot_dates_set]
        status['missing_snapshots'] = sorted(missing_snapshots)

        # 🔧 待更新天数：快照日期到今天的差值
        if latest_snapshot:
            if latest_snapshot < today:
                status['pending_update_days'] = (today - latest_snapshot).days
            else:
                status['pending_update_days'] = 0
        else:
            status['pending_update_days'] = (today - start_date).days if start_date <= today else 0

        # 最近修改记录（只显示起始日期之后的）
        recent_changes = pd.read_sql(
            text("""
                SELECT flower, change_date, old_stock, new_stock, operator, changed_at
                FROM inventory_change_log
                WHERE change_date >= :start
                ORDER BY changed_at DESC
                LIMIT 5
            """),
            conn,
            params={"start": start_date}
        )
        status['recent_changes'] = recent_changes if not recent_changes.empty else None

        return status
def get_missing_report_dates():
    """获取缺失日报的日期（有订单但没生成日报），只显示起始日期之后"""
    from system_service import get_system_start_date

    start_date = get_system_start_date()

    with engine.connect() as conn:
        # 只获取起始日期之后的订单
        order_dates = conn.execute(
            text("""
                SELECT DISTINCT DATE(order_time)
                FROM data2026
                WHERE order_time IS NOT NULL
                  AND DATE(order_time) >= :start
                ORDER BY DATE(order_time)
            """),
            {"start": start_date}
        ).fetchall()
        order_dates_set = {row[0] for row in order_dates if row[0] is not None}

        # 已生成日报的日期（只取起始日期之后的）
        report_dates = conn.execute(
            text("""
                SELECT DISTINCT report_date
                FROM daily_report_cache
                WHERE report_date >= :start
                ORDER BY report_date
            """),
            {"start": start_date}
        ).fetchall()
        report_dates_set = {row[0] for row in report_dates if row[0] is not None}

        # 缺失日报 = 有订单但没生成日报
        missing = [d for d in order_dates_set if d not in report_dates_set]
        return sorted(missing)


# =============================================
# 拿货建议：基于近7天销量 + 当前库存，建议每种花型拿多少米
# =============================================
def get_restock_suggestions(target_days=7):
    """
    获取所有花型的拿货建议，按紧急程度排序。

    算法：
    - 近7天日均销量 = AVG(daily_report_cache.total_meters) over last 7 days
    - 当前库存 = 最新快照库存
    - 可售天数 = 当前库存 / 日均销量（日均销量=0 则为 ∞）
    - 建议拿货 = max(0, 日均销量 × target_days - 当前库存)，向上取整到 0.5 米

    排序：可售天数升序 → 日均销量降序（最紧急的排最前）

    返回 DataFrame 列：
    ['花型', '当前库存(米)', '近7天日均销量', '可售天数', '建议拿货(米)']
    """
    from add_del_flower import get_available_flowers
    import math

    # 获取可用花型（未删除的）
    available_df = get_available_flowers()
    if available_df.empty:
        return pd.DataFrame(columns=['花型', '当前库存(米)', '近7天日均销量', '可售天数', '建议拿货(米)'])

    available_flowers = available_df['flower'].tolist()

    # 获取最新快照日期
    latest_date = get_latest_snapshot_date()
    if not latest_date:
        return pd.DataFrame(columns=['花型', '当前库存(米)', '近7天日均销量', '可售天数', '建议拿货(米)'])

    # 获取当前库存
    inv_df = get_inventory_snapshot(latest_date)
    if inv_df.empty:
        return pd.DataFrame(columns=['花型', '当前库存(米)', '近7天日均销量', '可售天数', '建议拿货(米)'])

    # 只保留可用花型
    inv_df = inv_df[inv_df['花型'].isin(available_flowers)].copy()

    # 获取近7天日均销量（基于日报缓存）
    with engine.connect() as conn:
        avg_sales = pd.read_sql(
            text("""
                SELECT flower, AVG(total_meters) as avg_daily
                FROM daily_report_cache
                WHERE report_date >= DATE_SUB(CURDATE(), INTERVAL 7 DAY)
                GROUP BY flower
            """),
            conn
        )
    avg_map = dict(zip(avg_sales['flower'], avg_sales['avg_daily']))

    # 计算各指标
    inv_df['近7天日均销量'] = inv_df['花型'].apply(lambda x: round(avg_map.get(x, 0), 2))
    inv_df['可售天数_raw'] = inv_df.apply(
        lambda row: row['库存'] / row['近7天日均销量'] if row['近7天日均销量'] > 0 else float('inf'),
        axis=1
    )

    # 建议拿货量：max(0, 日均销量 × target_days - 当前库存)，向上取整到 0.5
    def calc_suggest(row):
        if row['近7天日均销量'] <= 0:
            return 0.0
        need = row['近7天日均销量'] * target_days - row['库存']
        if need <= 0:
            return 0.0
        # 向上取整到 0.5
        return math.ceil(need * 2) / 2

    inv_df['建议拿货(米)'] = inv_df.apply(calc_suggest, axis=1)

    # 可售天数格式化：∞ 显示为 None，方便排序
    inv_df['可售天数'] = inv_df['可售天数_raw'].apply(
        lambda x: round(x, 1) if x != float('inf') else None
    )

    # 排序：可售天数升序（None 排最后），日均销量降序
    inv_df['_sort_days'] = inv_df['可售天数_raw'].apply(lambda x: x if x != float('inf') else 999999)
    inv_df = inv_df.sort_values(['_sort_days', '近7天日均销量'], ascending=[True, False])

    # 重命名列
    result = inv_df[['花型', '库存', '近7天日均销量', '可售天数', '建议拿货(米)']].copy()
    result = result.rename(columns={'库存': '当前库存(米)'})
    result = result.reset_index(drop=True)

    return result


def check_flower_active(flower):
    """检查花型是否可用（未删除）"""
    with engine.connect() as conn:
        result = conn.execute(
            text("SELECT is_deleted FROM product_cost WHERE flower = :f"),
            {"f": flower}
        ).fetchone()
        if not result:
            return (False, f"花型「{flower}」不存在")
        if result[0] == 1:
            return (False, f"花型「{flower}」已被删除，无法操作")
        return (True, "")
# =============================================
# 测试代码
# =============================================
if __name__ == "__main__":
    print("🧪 测试库存服务...")
    print(get_inventory_report().head())
