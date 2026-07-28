import pandas as pd
from sqlalchemy import text
from mysql_conn import engine
from datetime import datetime, timedelta

# =============================================
# 1. 查询花型
# =============================================
def get_all_flowers(include_deleted=False):
    """
    获取所有花型列表
    include_deleted=True  → 返回全部（含已删除），用于管理页面
    include_deleted=False → 只返回正常花型，用于业务下拉框
    """
    with engine.connect() as conn:
        if include_deleted:
            query = text("""
                SELECT flower, cost_per_meter, is_deleted, delete_time, delete_effect_date
                FROM product_cost
                ORDER BY is_deleted ASC, flower ASC
            """)
        else:
            query = text("""
                SELECT flower, cost_per_meter
                FROM product_cost
                WHERE is_deleted = 0
                ORDER BY flower ASC
            """)
        df = pd.read_sql(query, conn)
    return df


def get_available_flowers():
    """获取可用花型（用于下拉选择），只返回未删除"""
    with engine.connect() as conn:
        df = pd.read_sql(
            text("SELECT flower, cost_per_meter FROM product_cost WHERE is_deleted = 0 ORDER BY flower ASC"),
            conn
        )
    return df


# =============================================
# 2. 新增花型
# =============================================
def add_flower(flower_name, cost_per_meter=0.0, operator="system"):
    """
    新增花型
    同步初始化：product_cost、inventory、inventory_base、inventory_snapshot
    返回 (成功/失败, 消息)
    """
    flower_name = flower_name.strip()
    if not flower_name:
        return (False, "花型名称不能为空")

    with engine.connect() as conn:
        trans = conn.begin()
        try:
            # 1. 检查是否已存在（含已删除的）
            existing = conn.execute(
                text("SELECT is_deleted FROM product_cost WHERE flower = :f"),
                {"f": flower_name}
            ).fetchone()

            if existing:
                if existing[0] == 1:
                    # 已删除的花型，直接恢复
                    conn.execute(
                        text("""
                            UPDATE product_cost 
                            SET is_deleted = 0, 
                                delete_time = NULL, 
                                delete_effect_date = NULL,
                                cost_per_meter = :cost
                            WHERE flower = :f
                        """),
                        {"f": flower_name, "cost": cost_per_meter}
                    )
                    # 写入流水 - 恢复花型
                    conn.execute(
                        text("""
                            INSERT INTO inventory_log 
                            (flower, change_type, change_qty, before_stock, after_stock, reference, operator)
                            VALUES (:f, '恢复花型', 0, 0, 0, '新增时发现已删除，自动恢复并更新成本', :op)
                        """),
                        {"f": flower_name, "op": operator}
                    )
                    trans.commit()
                    return (True, f"✅ 花型「{flower_name}」已恢复并更新成本")
                else:
                    return (False, f"❌ 花型「{flower_name}」已存在")

            # 2. 插入 product_cost
            conn.execute(
                text("""
                    INSERT INTO product_cost (flower, cost_per_meter, update_time)
                    VALUES (:f, :cost, NOW())
                """),
                {"f": flower_name, "cost": cost_per_meter}
            )

            # 3. 同步初始化 inventory 实时库存表（默认0库存）
            conn.execute(
                text("""
                    INSERT INTO inventory (flower, current_stock, alert_days, supplier_lead_time)
                    VALUES (:f, 0, 7, 3)
                """),
                {"f": flower_name}
            )

            # 4. 同步初始化 inventory_base 基准库存表（默认0）
            base_date = conn.execute(
                text("SELECT MIN(base_date) FROM inventory_base")
            ).scalar()
            if base_date:
                conn.execute(
                    text("""
                        INSERT INTO inventory_base (flower, base_stock, base_date)
                        VALUES (:f, 0, :d)
                    """),
                    {"f": flower_name, "d": base_date}
                )

                # 5. 补全从基准日期到最新快照日期的所有快照（库存为0）
                latest_snap = conn.execute(
                    text("SELECT MAX(snapshot_date) FROM inventory_snapshot")
                ).scalar()
                if latest_snap:
                    current = base_date
                    while current <= latest_snap:
                        conn.execute(
                            text("""
                                INSERT INTO inventory_snapshot (flower, snapshot_date, stock, updated_by)
                                VALUES (:f, :d, 0, :op)
                                ON DUPLICATE KEY UPDATE stock = stock
                            """),
                            {"f": flower_name, "d": current, "op": operator}
                        )
                        current += timedelta(days=1)

            # 6. 写入流水 - 记录新增花型
            conn.execute(
                text("""
                    INSERT INTO inventory_log 
                    (flower, change_type, change_qty, before_stock, after_stock, reference, operator)
                    VALUES (:f, '新增花型', 0, 0, 0, '系统新增花型', :op)
                """),
                {"f": flower_name, "op": operator}
            )

            trans.commit()
            return (True, f"✅ 花型「{flower_name}」新增成功")
        except Exception as e:
            trans.rollback()
            return (False, f"❌ 新增失败：{str(e)}")


# =============================================
# 3. 删除花型（软删除 - 立即生效）
# =============================================
def delete_flower(flower_name, operator="system"):
    """
    软删除花型
    - 立即生效：禁止入库/出库等操作
    - 报表层面：历史日报保留，删除当天及之后的日报不包含
    """
    if not flower_name:
        return (False, "请选择花型")

    with engine.connect() as conn:
        trans = conn.begin()
        try:
            # 1. 检查花型状态
            existing = conn.execute(
                text("SELECT is_deleted FROM product_cost WHERE flower = :f"),
                {"f": flower_name}
            ).fetchone()
            if not existing:
                return (False, f"❌ 花型「{flower_name}」不存在")
            if existing[0] == 1:
                return (False, f"❌ 花型「{flower_name}」已处于删除状态")

            # 2. 获取当前库存信息（用于流水记录）
            stock_info = conn.execute(
                text("SELECT current_stock FROM inventory WHERE flower = :f"),
                {"f": flower_name}
            ).fetchone()
            current_stock = float(stock_info[0]) if stock_info else 0.0

            # 3. 获取花型成本（用于流水记录）
            cost_info = conn.execute(
                text("SELECT cost_per_meter FROM product_cost WHERE flower = :f"),
                {"f": flower_name}
            ).fetchone()
            cost = float(cost_info[0]) if cost_info else 0.0

            # 4. 软删除 - 立即生效
            conn.execute(text("""
                UPDATE product_cost 
                SET is_deleted = 1, 
                    delete_time = NOW(), 
                    delete_effect_date = CURDATE()
                WHERE flower = :f
            """), {"f": flower_name})

            # 5. 写入流水 - 记录删除花型
            conn.execute(
                text("""
                    INSERT INTO inventory_log 
                    (flower, change_type, change_qty, before_stock, after_stock, reference, operator)
                    VALUES (
                        :f, 
                        '删除花型', 
                        0, 
                        :before, 
                        0, 
                        CONCAT('删除花型，删除时库存：', :before, '米，成本：', :cost, '元/米，生效日期：', CURDATE()), 
                        :op
                    )
                """),
                {
                    "f": flower_name,
                    "before": current_stock,
                    "cost": cost,
                    "op": operator
                }
            )

            trans.commit()
            return (True, f"✅ 花型「{flower_name}」已删除，立即生效（删除时库存：{current_stock}米）")
        except Exception as e:
            trans.rollback()
            return (False, f"❌ 删除失败：{str(e)}")


# =============================================
# 4. 恢复花型
# =============================================
def restore_flower(flower_name, operator="system"):
    """恢复已删除花型，清空生效日期"""
    if not flower_name:
        return (False, "请选择花型")

    with engine.connect() as conn:
        trans = conn.begin()
        try:
            # 1. 检查花型状态
            existing = conn.execute(
                text("SELECT is_deleted, delete_effect_date FROM product_cost WHERE flower = :f"),
                {"f": flower_name}
            ).fetchone()
            if not existing:
                return (False, f"❌ 花型「{flower_name}」不存在")
            if existing[0] == 0:
                return (False, f"❌ 花型「{flower_name}」未被删除")

            delete_effect_date = existing[1]

            # 2. 恢复花型
            conn.execute(text("""
                UPDATE product_cost
                SET is_deleted = 0,
                    delete_time = NULL,
                    delete_effect_date = NULL
                WHERE flower = :f
            """), {"f": flower_name})

            # 3. 获取当前库存（恢复后库存保持原样）
            stock_info = conn.execute(
                text("SELECT current_stock FROM inventory WHERE flower = :f"),
                {"f": flower_name}
            ).fetchone()
            current_stock = float(stock_info[0]) if stock_info else 0.0

            # 4. 写入流水 - 记录恢复花型
            conn.execute(
                text("""
                    INSERT INTO inventory_log 
                    (flower, change_type, change_qty, before_stock, after_stock, reference, operator)
                    VALUES (
                        :f, 
                        '恢复花型', 
                        0, 
                        :before, 
                        :before, 
                        CONCAT('恢复已删除花型，原删除生效日期：', :delete_date, '，当前库存：', :before, '米'), 
                        :op
                    )
                """),
                {
                    "f": flower_name,
                    "before": current_stock,
                    "delete_date": delete_effect_date.strftime('%Y-%m-%d') if delete_effect_date else '未知',
                    "op": operator
                }
            )

            trans.commit()
            return (True, f"✅ 花型「{flower_name}」已恢复（当前库存：{current_stock}米）")
        except Exception as e:
            trans.rollback()
            return (False, f"❌ 恢复失败：{str(e)}")