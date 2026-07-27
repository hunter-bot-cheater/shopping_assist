# populate_refund_details.py
import pandas as pd
from sqlalchemy import text
from mysql_conn import engine
from make_daily import extract_flower_from_spec, extract_meter_from_spec

def sync_refund_details():
    print("📋 正在同步退款明细...")

    with engine.connect() as conn:
        # 查询已发货（order_status 包含“已发货”）且退款成功（after_sale_status 包含“退款成功”）的订单
        df = pd.read_sql(
            text("""
                SELECT 
                    order_no, 
                    product_spec, 
                    product_quantity,
                    merchant_income,
                    after_sale_status,
                    order_time
                FROM data2026
                WHERE (order_status LIKE '%已发货%' or order_status LIKE '%已收货%')
                  AND after_sale_status LIKE '%退款成功%'
            """),
            conn
        )

        if df.empty:
            print("✅ 没有符合条件的退款订单需要同步")
            return

        print(f"📦 找到 {len(df)} 条退款记录")

        # 提取花型和米数
        df['flower'] = df['product_spec'].apply(extract_flower_from_spec)
        df['meters'] = df['product_spec'].apply(extract_meter_from_spec)
        df.loc[df['meters'] == 0, 'meters'] = 1
        df['refund_meters'] = df['meters'] * df['product_quantity']
        df['refund_amount'] = df['merchant_income'].round(2)

        # 按 (order_no, flower) 分组聚合
        grouped = df.groupby(['order_no', 'flower'], as_index=False).agg({
            'refund_meters': 'sum',
            'refund_amount': 'sum',
            'product_spec': 'first',
            'product_quantity': 'sum',
            'after_sale_status': 'first',
            'order_time': 'first'
        })

        # 处理 order_time 的 NaT → None
        grouped['order_time'] = grouped['order_time'].replace({pd.NaT: None})

        total = 0
        for _, row in grouped.iterrows():
            sql = text("""
                INSERT INTO refund_detail 
                (order_no, flower, product_spec, product_quantity, 
                 refund_meters, refund_amount, after_sale_status, refund_time)
                VALUES (:order_no, :flower, :spec, :qty, :meters, :amount, :status, :time)
                ON DUPLICATE KEY UPDATE
                    product_spec = VALUES(product_spec),
                    product_quantity = VALUES(product_quantity),
                    refund_meters = VALUES(refund_meters),
                    refund_amount = VALUES(refund_amount),
                    after_sale_status = VALUES(after_sale_status),
                    refund_time = VALUES(refund_time)
            """)
            conn.execute(sql, {
                "order_no": row['order_no'],
                "flower": row['flower'],
                "spec": row['product_spec'],
                "qty": int(row['product_quantity']),
                "meters": float(row['refund_meters']),
                "amount": float(row['refund_amount']),
                "status": row['after_sale_status'],
                "time": row['order_time']  # 已转为 None 或有效时间
            })
            total += 1

        conn.commit()
        print(f"✅ 同步完成：共 {total} 条退款明细")

if __name__ == "__main__":
    sync_refund_details()