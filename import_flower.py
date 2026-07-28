# import_cost.py
import pandas as pd
from sqlalchemy import text
from mysql_conn import engine


def import_cost_excel(file_path="成本表(1).xlsx"):
    # 1. 读取Excel（无表头，自动命名列）
    print(f"正在读取文件: {file_path}")
    try:
        df = pd.read_excel(file_path, sheet_name="Sheet1", header=None)
    except Exception as e:
        print(f"❌ 读取文件失败: {e}")
        return

    # 2. 重命名列 + 数据清洗
    df.columns = ["flower", "cost_per_meter"]
    df["flower"] = df["flower"].astype(str).str.strip()
    df["cost_per_meter"] = pd.to_numeric(df["cost_per_meter"], errors="coerce").fillna(0.0)

    # 过滤空行
    df = df[df["flower"].notna() & (df["flower"] != "")]
    total = len(df)
    print(f"共读取到 {total} 条花型成本数据")

    # 3. 批量导入数据库（UPSERT：存在则更新，不存在则新增）
    added = 0  # 新增数量
    updated = 0  # 更新数量
    with engine.begin() as conn:
        for _, row in df.iterrows():
            flower = row["flower"]
            cost = float(row["cost_per_meter"])

            # 插入/更新成本表：已删除的自动恢复
            sql = text("""
                INSERT INTO product_cost (flower, cost_per_meter, update_time, is_deleted, delete_time)
                VALUES (:f, :c, NOW(), 0, NULL)
                ON DUPLICATE KEY UPDATE
                    cost_per_meter = VALUES(cost_per_meter),
                    update_time = NOW(),
                    is_deleted = 0,
                    delete_time = NULL
            """)
            result = conn.execute(sql, {"f": flower, "c": cost})

            # 判断是新增还是更新
            if result.rowcount == 1:
                added += 1
                # 新花型：同步初始化 inventory 实时库存表（默认0库存）
                conn.execute(text("""
                    INSERT IGNORE INTO inventory (flower, current_stock, alert_days, supplier_lead_time)
                    VALUES (:f, 0, 7, 3)
                """), {"f": flower})
            else:
                updated += 1

    print("\n" + "=" * 50)
    print(f"✅ 导入完成！")
    print(f"   新增花型: {added} 个")
    print(f"   更新成本: {updated} 个")
    print(f"   总计处理: {total} 个")
    print("=" * 50)
    print("💡 新花型默认库存为0，可通过「入库登记」补充库存")


if __name__ == "__main__":
    # 如果文件名不一样，修改这里的路径
    import_cost_excel(r"C:\Users\zpy53\Desktop\成本表(1).xlsx")