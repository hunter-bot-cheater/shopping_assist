# init_inventory.py
from sqlalchemy import text
from mysql_conn import engine

from inventory_service import init_inventory_base
def init_inventory_all_flowers(default_stock=200, operator="system"):
    """
    将所有花型的库存初始化为 default_stock 米
    如果花型已在 inventory 表中，则更新；如果不存在，则插入
    """
    with engine.connect() as conn:
        trans = conn.begin()
        try:
            # 1. 从 product_cost 获取所有花型
            flowers = conn.execute(
                text("SELECT flower FROM product_cost ORDER BY flower")
            ).fetchall()

            if not flowers:
                print("❌ product_cost 表为空，请先导入花型成本表")
                return

            flower_list = [row[0] for row in flowers]
            print(f"📋 找到 {len(flower_list)} 个花型")

            total = 0
            for flower in flower_list:
                # 检查该花型是否已在 inventory 表中
                existing = conn.execute(
                    text("SELECT current_stock FROM inventory WHERE flower = :f"),
                    {"f": flower}
                ).fetchone()

                if existing:
                    old_stock = float(existing[0])
                    # 如果已经是 200，跳过
                    if abs(old_stock - default_stock) < 0.001:
                        print(f"⏭️ {flower}: 已是 {default_stock} 米，跳过")
                        continue

                    # 更新库存
                    conn.execute(
                        text("UPDATE inventory SET current_stock = :new WHERE flower = :f"),
                        {"new": default_stock, "f": flower}
                    )

                    # 写流水
                    conn.execute(
                        text("""
                            INSERT INTO inventory_log 
                            (flower, change_type, change_qty, before_stock, after_stock, reference, operator)
                            VALUES (:f, '手动调整', :qty, :before, :after, :ref, :op)
                        """),
                        {
                            "f": flower,
                            "qty": default_stock - old_stock,
                            "before": old_stock,
                            "after": default_stock,
                            "ref": "批量初始化库存为200米",
                            "op": operator
                        }
                    )
                    print(f"✅ {flower}: {old_stock} → {default_stock} 米")
                    total += 1
                else:
                    # 不存在，插入新记录
                    conn.execute(
                        text("""
                            INSERT INTO inventory (flower, current_stock, alert_days, supplier_lead_time)
                            VALUES (:f, :stock, 7, 3)
                        """),
                        {"f": flower, "stock": default_stock}
                    )

                    # 写流水（初始化）
                    conn.execute(
                        text("""
                            INSERT INTO inventory_log 
                            (flower, change_type, change_qty, before_stock, after_stock, reference, operator)
                            VALUES (:f, '初始化', :qty, 0, :after, :ref, :op)
                        """),
                        {
                            "f": flower,
                            "qty": default_stock,
                            "after": default_stock,
                            "ref": "批量初始化库存为200米",
                            "op": operator
                        }
                    )
                    print(f"✅ {flower}: 新建记录，初始 {default_stock} 米")
                    total += 1

            trans.commit()
            print(f"\n🎉 初始化完成！共处理 {total} 个花型，全部设为 {default_stock} 米")

        except Exception as e:
            trans.rollback()
            print(f"❌ 初始化失败：{e}")
            raise


if __name__ == "__main__":
    # 初始化所有花型库存为 200 米
    init_inventory_all_flowers(default_stock=0, operator="admin")
    success, msg = init_inventory_base(
        base_date='2026-07-01',
        base_stock=0,
        operator='admin'
    )
    print(msg)