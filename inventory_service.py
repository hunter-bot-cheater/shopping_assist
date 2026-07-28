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
# 核心功能 1：手动入库（增加库存）+ 自动补录缺口
# =============================================
def add_stock(flower, qty, reference="手动入库", operator="system", target_date=None):
    """
    增加库存（采购入库/手动补货）
    同时更新 inventory 表和 inventory_snapshot 表
    target_date: 入库生效日期，从该日期起所有快照 +qty，默认今天
    """
    if qty <= 0:
        raise ValueError("入库数量必须大于 0")

    active, msg = check_flower_active(flower)
    if not active:
        raise ValueError(msg)

    # 🔧 处理 target_date
    if target_date is None:
        target_date = datetime.now().date()
    elif isinstance(target_date, str):
        try:
            target_date = datetime.strptime(target_date, '%Y-%m-%d').date()
        except ValueError:
            target_date = datetime.strptime(target_date, '%Y%m%d').date()

    with engine.connect() as conn:
        trans = conn.begin()
        try:
            current = get_current_stock(flower)
            new_stock = current + qty

            # 1. 更新旧表（实时库存）
            conn.execute(
                text("UPDATE inventory SET current_stock = :new WHERE flower = :f"),
                {"new": new_stock, "f": flower}
            )

            # 2. 确保目标日期及之后有快照记录
            check_snapshot = conn.execute(
                text("SELECT COUNT(*) FROM inventory_snapshot WHERE flower = :f AND snapshot_date >= :d"),
                {"f": flower, "d": target_date}
            ).scalar()

            if check_snapshot == 0:
                print(f"⚠️ {flower} 从 {target_date} 起没有快照记录，正在补全...")
                from inventory_service import fill_missing_snapshots
                fill_missing_snapshots(operator=operator)

            # 3. 🔧 从 target_date 起的所有快照都 +qty，且不低于 0
            result = conn.execute(
                text("""
                    UPDATE inventory_snapshot
                    SET stock = GREATEST(stock + :qty, 0), updated_by = :op, updated_at = CURRENT_TIMESTAMP
                    WHERE flower = :f AND snapshot_date >= :d
                """),
                {"qty": qty, "op": operator, "f": flower, "d": target_date}
            )
            print(f"🔍 快照表更新影响行数：{result.rowcount}（从 {target_date} 起）")

            # 4. 写入流水
            conn.execute(
                text("""
                    INSERT INTO inventory_log
                    (flower, change_type, change_qty, before_stock, after_stock, reference, operator)
                    VALUES (:f, '入库', :qty, :before, :after, :ref, :op)
                """),
                {"f": flower, "qty": qty, "before": current, "after": new_stock, "ref": reference, "op": operator}
            )

            trans.commit()
            print(f"✅ 入库成功：{flower} 增加 {qty} 米（当前库存：{new_stock}，生效日期：{target_date}）")

        except Exception as e:
            trans.rollback()
            raise e
# =============================================
# 核心功能 2：销售出库（减少库存）
# =============================================
def deduct_stock(flower, qty, reference="销售出库", operator="system", report_date=None):
    """
    手动出库：直接扣减库存，同时更新 inventory 和 inventory_snapshot
    report_date: 出库日期，从该日期起所有快照 -qty（库存不会低于 0）
    """
    if qty <= 0:
        raise ValueError("出库数量必须大于 0")

    active, msg = check_flower_active(flower)
    if not active:
        raise ValueError(msg)

    # 🔧 将 report_date 统一转换为 date 对象
    if report_date is not None:
        if isinstance(report_date, str):
            try:
                report_date = datetime.strptime(report_date, '%Y-%m-%d').date()
            except ValueError:
                report_date = datetime.strptime(report_date, '%Y%m%d').date()
        # 如果已经是 date 对象，保持不变
    else:
        report_date = datetime.now().date()

    with engine.connect() as conn:
        trans = conn.begin()
        try:
            current = get_current_stock(flower)
            # 🔧 库存不低于 0：扣减后若为负数则保持为 0
            new_stock = max(current - qty, 0)

            # 1. 更新旧表（实时库存，不出现负数）
            conn.execute(
                text("UPDATE inventory SET current_stock = :new WHERE flower = :f"),
                {"new": new_stock, "f": flower}
            )

            # 2. 确保目标日期及之后有快照记录
            check_snapshot = conn.execute(
                text("SELECT COUNT(*) FROM inventory_snapshot WHERE flower = :f AND snapshot_date >= :d"),
                {"f": flower, "d": report_date}
            ).scalar()

            if check_snapshot == 0:
                print(f"⚠️ {flower} 从 {report_date} 起没有快照记录，正在补全...")
                from inventory_service import fill_missing_snapshots
                fill_missing_snapshots(operator=operator)

            # 3. 🔧 从 report_date 起的所有快照都 -qty，但不低于 0
            result = conn.execute(
                text("""
                    UPDATE inventory_snapshot
                    SET stock = GREATEST(stock - :qty, 0), updated_by = :op, updated_at = CURRENT_TIMESTAMP
                    WHERE flower = :f AND snapshot_date >= :d
                """),
                {"qty": qty, "op": operator, "f": flower, "d": report_date}
            )
            print(f"🔍 快照表更新影响行数：{result.rowcount}（从 {report_date} 起）")

            # 4. 写入流水（记录实际扣减量和结果）
            actual_deduct = current - new_stock  # 实际扣减量（若库存不足则小于 qty）
            conn.execute(
                text("""
                    INSERT INTO inventory_log
                    (flower, change_type, change_qty, before_stock, after_stock, reference, operator)
                    VALUES (:f, '销售出库', :qty, :before, :after, :ref, :op)
                """),
                {"f": flower, "qty": -actual_deduct, "before": current, "after": new_stock, "ref": reference, "op": operator}
            )

            trans.commit()

            if current < qty:
                print(f"⚠️ {flower} 库存不足：当前 {current} 米，出库 {qty} 米，实际扣减 {actual_deduct} 米，库存保持为 0")
            else:
                print(f"✅ {flower} 扣减 {qty} 米（剩余：{new_stock}）")

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

                conn.execute(
                    text("""
                        INSERT INTO inventory_log
                        (flower, change_type, change_qty, before_stock, after_stock, reference, operator)
                        VALUES (:f, '手动调整', :qty, :before, :after, :ref, :op)
                    """),
                    {
                        "f": flower,
                        "qty": -change_qty,
                        "before": current,
                        "after": target_stock,
                        "ref": f"回退日报 {target_date}（重新生成前）",
                        "op": operator
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
                    DATE(created_at) AS 日期,
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
                    DATE(created_at) AS 日期,
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
                    (flower, change_type, change_qty, before_stock, after_stock, reference, operator)
                    VALUES (:f, '报损', :qty, :before, :after, :ref, :op)
                """),
                {"f": flower, "qty": -qty, "before": current, "after": new_stock, "ref": reason, "op": operator}
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
    整体回退库存到指定日期
    删除目标日期之后的所有快照，库存恢复到该日期状态
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

            # 2. 统计将被删除的快照数量
            to_delete = conn.execute(
                text("SELECT COUNT(*) FROM inventory_snapshot WHERE snapshot_date > :d"),
                {"d": target_date}
            ).scalar()

            if to_delete == 0:
                trans.rollback()
                return (False, f"ℹ️ {target_date} 之后没有快照数据，无需回退", 0)

            # 3. 🔧 记录回退操作到库存流水
            flower_count = conn.execute(
                text("SELECT COUNT(DISTINCT flower) FROM inventory_snapshot WHERE snapshot_date = :d"),
                {"d": target_date}
            ).scalar()

            conn.execute(
                text("""
                    INSERT INTO inventory_log
                    (flower, change_type, change_qty, before_stock, after_stock, reference, operator)
                    VALUES ('系统', '手动调整', 0, 0, 0, :ref, :op)
                """),
                {
                    "ref": f"回退库存至 {target_date}，删除 {to_delete} 条快照记录",
                    "op": operator
                }
            )

            # 4. 删除目标日期之后的所有快照
            deleted = conn.execute(
                text("DELETE FROM inventory_snapshot WHERE snapshot_date > :d"),
                {"d": target_date}
            ).rowcount

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
                    (flower, change_type, change_qty, before_stock, after_stock, reference, operator)
                    VALUES (:f, '手动调整', :qty, :before, :after, :ref, :op)
                """),
                {
                    "f": flower,
                    "qty": diff,
                    "before": current,
                    "after": target_stock,
                    "ref": reference,
                    "op": operator
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

        # 执行补全：逐天计算快照
        filled_count = 0
        current = start_date_compute

        while current <= up_to_date:
            # 获取前一天各花型的库存
            prev_date = current - timedelta(days=1)
            if prev_date < start_date:
                prev_df = pd.read_sql(
                    text("SELECT flower, base_stock as stock FROM inventory_base"),
                    conn
                )
                print(f"🔍 [DEBUG] {current} 前一天使用基准库存")
            else:
                prev_df = pd.read_sql(
                    text("SELECT flower, stock FROM inventory_snapshot WHERE snapshot_date = :d"),
                    conn,
                    params={"d": prev_date}
                )
                if prev_df.empty:
                    prev_df = pd.read_sql(
                        text("SELECT flower, base_stock as stock FROM inventory_base"),
                        conn
                    )
                    print(f"🔍 [DEBUG] {current} 前一天快照不存在，使用基准库存")
                else:
                    print(f"🔍 [DEBUG] {current} 前一天快照存在，共 {len(prev_df)} 条")

            prev_stock = dict(zip(prev_df['flower'], prev_df['stock']))

            # 🔧 获取当天的日报销售数据（有则扣减，无则为0，不影响快照延续）
            sales_df = pd.read_sql(
                text("SELECT flower, total_meters FROM daily_report_cache WHERE report_date = :d"),
                conn,
                params={"d": current}
            )
            sales = dict(zip(sales_df['flower'], sales_df['total_meters']))

            # 计算当天快照
            for flower in flowers:
                stock = prev_stock.get(flower, 0) - sales.get(flower, 0)
                if stock < 0:
                    stock = 0

                conn.execute(
                    text("""
                        INSERT INTO inventory_snapshot (flower, snapshot_date, stock, updated_by)
                        VALUES (:f, :d, :stock, :op)
                        ON DUPLICATE KEY UPDATE
                            stock = VALUES(stock),
                            updated_by = VALUES(updated_by),
                            updated_at = CURRENT_TIMESTAMP
                    """),
                    {"f": flower, "d": current, "stock": stock, "op": operator}
                )

            filled_count += 1
            print(f"  ✅ {current} 快照已生成")
            current += timedelta(days=1)
        conn.commit()
        print(f"✅ 已补全 {filled_count} 天的快照（截至 {up_to_date.strftime('%Y-%m-%d')}）")
        return (filled_count, "success", f"✅ 已补全 {filled_count} 天的快照（截至 {up_to_date.strftime('%Y-%m-%d')}）")


def update_inventory_snapshot(flower, target_date, new_stock, operator='system', reason='手动调整'):
    """
    修改某天某个花型的库存，并联动更新该日期之后的所有日期
    返回：(成功/失败, 消息, 受影响的天数)
    """
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

            # 2. 更新该日快照
            conn.execute(
                text("""
                    UPDATE inventory_snapshot
                    SET stock = :new, updated_by = :op, updated_at = CURRENT_TIMESTAMP
                    WHERE flower = :f AND snapshot_date = :d
                """),
                {"new": new_stock, "op": operator, "f": flower, "d": target_date}
            )

            # 3. 🔧 更新后续所有日期的快照（加上 delta，但不低于 0）
            affected = conn.execute(
                text("""
                    UPDATE inventory_snapshot
                    SET stock = GREATEST(stock + :delta, 0), updated_by = :op, updated_at = CURRENT_TIMESTAMP
                    WHERE flower = :f AND snapshot_date > :d
                """),
                {"delta": delta, "op": operator, "f": flower, "d": target_date}
            ).rowcount

            # 4. 🔧 新增：写入 inventory_log（库存流水）
            # 获取修改后当天该花型的库存（即 new_stock）
            # 但注意：如果 affected > 0，后续日期也变了，但当天快照就是 new_stock
            # 写入一条流水记录，标明是"快照调整"
            conn.execute(
                text("""
                    INSERT INTO inventory_log
                    (flower, change_type, change_qty, before_stock, after_stock, reference, operator)
                    VALUES (:f, '手动调整', :qty, :before, :after, :ref, :op)
                """),
                {
                    "f": flower,
                    "qty": delta,
                    "before": old_stock,
                    "after": new_stock,
                    "ref": f"快照调整 {target_date}（{reason}）",
                    "op": operator
                }
            )

            # 5. 记录变更日志（inventory_change_log）
            conn.execute(
                text("""
                    INSERT INTO inventory_change_log
                    (flower, change_date, old_stock, new_stock, reason, operator)
                    VALUES (:f, :d, :old, :new, :reason, :op)
                """),
                {"f": flower, "d": target_date, "old": old_stock, "new": new_stock, "reason": reason, "op": operator}
            )

            trans.commit()
            return (True,
                    f"✅ {flower} 在 {target_date} 库存从 {old_stock} 调整为 {new_stock}（{'+' if delta > 0 else ''}{delta}），影响 {affected} 天",
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
