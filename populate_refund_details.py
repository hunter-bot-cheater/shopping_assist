# populate_refund_details.py
import pandas as pd
from sqlalchemy import text
from mysql_conn import engine
from make_daily import extract_meter_from_spec, assign_flowers, load_cost_map


def _status_label(order_status, after_sale_status):
    """把 order_status(已发货/已收货) 与 after_sale_status(退款成功) 拼成展示用字符串。
    同时规整历史遗留：若 after_sale_status 已是新格式（包含 / 或前缀），原样保留。
    """
    if not order_status:
        order_status = ""
    if "已发货" in order_status:
        head = "已发货"
    elif "已收货" in order_status:
        head = "已收货"
    else:
        head = order_status.strip() or "未知"
    aft = (after_sale_status or "").strip()
    # 若 after_sale_status 已包含 / 已发货/已收货 等关键字，认为是新格式，原样
    if "/" in aft or aft.startswith("已发货") or aft.startswith("已收货"):
        return aft
    return f"{head}/{aft}" if aft else head


def _backfill_status_label(conn):
    """历史脏数据重写：refund_detail.after_sale_status 仅含「退款成功」且 data2026
    源订单是已发货/已收货的，按当前 order_status 重写为 拼接格式。
    """
    rows = conn.execute(text("""
        SELECT r.order_no, r.after_sale_status,
               (SELECT order_status FROM data2026 d WHERE d.order_no = r.order_no LIMIT 1) AS os
        FROM refund_detail r
    """)).fetchall()
    if not rows:
        return 0
    upd = 0
    for r in rows:
        order_no, cur_status, os = r[0], r[1], r[2]
        if not os:
            continue
        if "/" in (cur_status or "") or "已发货" in (cur_status or "") or "已收货" in (cur_status or ""):
            continue
        new_label = _status_label(os, cur_status)
        if new_label and new_label != cur_status:
            conn.execute(
                text("UPDATE refund_detail SET after_sale_status = :s WHERE order_no = :o AND after_sale_status = :c"),
                {"s": new_label, "o": order_no, "c": cur_status}
            )
            upd += 1
    return upd


def sync_refund_details(cost_map=None):
    print("📋 正在同步退款明细...")

    with engine.connect() as conn:
        # ① 先清理：① 订单表中已不存在该订单的残留 ② 现存但源订单不再满足「已发货/已收货 退款成功」的记录
        # 依据：以 data2026 当前状态为准；已发货/已收货标记可能在订单状态或售后状态任一字段
        #（淘宝/抖音合成在售后状态如「已发货，退款成功」，订单状态为「交易关闭/已关闭」）
        cleanup = conn.execute(text("""
            DELETE r FROM refund_detail r
            LEFT JOIN data2026 d ON r.order_no = d.order_no
            WHERE d.order_no IS NULL
               OR NOT (
                    (d.order_status LIKE '%已发货%' OR d.order_status LIKE '%已收货%'
                     OR d.after_sale_status LIKE '%已发货%' OR d.after_sale_status LIKE '%已收货%')
                    AND d.after_sale_status LIKE '%退款成功%'
                    AND d.after_sale_status NOT LIKE '%未发货%'
               )
        """))
        cleaned = cleanup.rowcount or 0
        if cleaned:
            print(f"🧹 已清理 {cleaned} 条历史脏数据（非「已发货/已收货 退款成功」）")

        # ② 把历史「仅退款成功」无前缀的记录，按 data2026 当前 order_status 补齐前缀
        backfilled = _backfill_status_label(conn)
        if backfilled:
            print(f"✏️ 已重写 {backfilled} 条历史「售后状态」为「已发货/已收货 + 退款成功」格式")

        # ③ 查询已发货/已收货 且 退款成功 且 非未发货 的订单
        df = pd.read_sql(
            text("""
                SELECT
                    order_no,
                    product,
                    product_spec,
                    product_quantity,
                    merchant_income,
                    order_status,
                    after_sale_status,
                    order_time
                FROM data2026
                WHERE (order_status LIKE '%已发货%' OR order_status LIKE '%已收货%'
                       OR after_sale_status LIKE '%已发货%' OR after_sale_status LIKE '%已收货%')
                  AND after_sale_status LIKE '%退款成功%'
                  AND after_sale_status NOT LIKE '%未发货%'
            """),
            conn
        )

        if df.empty:
            print("✅ 没有符合条件的退款订单需要同步")
            return

        print(f"📦 找到 {len(df)} 条退款记录")

        # 花型与日报口径一致：四层匹配到成本表花型，匹配不上的归入「未匹配」
        if cost_map is None:
            cost_map = load_cost_map()
        df = assign_flowers(df, set(cost_map.keys()))

        # 提取花型和米数
        df['meters'] = df['product_spec'].apply(extract_meter_from_spec)
        df.loc[df['meters'] == 0, 'meters'] = 1
        df['refund_meters'] = df['meters'] * df['product_quantity']
        df['refund_amount'] = df['merchant_income'].round(2)

        # 按 (order_no, flower) 分组聚合
        grouped = df.groupby(['order_no', '花型'], as_index=False).agg({
            'refund_meters': 'sum',
            'refund_amount': 'sum',
            'product_spec': 'first',
            'product_quantity': 'sum',
            'order_status': 'first',
            'after_sale_status': 'first',
            'order_time': 'first'
        })

        # 处理 order_time 的 NaT → None
        grouped['order_time'] = grouped['order_time'].replace({pd.NaT: None})

        # 拼接「已发货/已收货」+「退款成功」为展示用售后状态
        grouped['after_sale_status'] = grouped.apply(
            lambda r: _status_label(r['order_status'], r['after_sale_status']),
            axis=1
        )

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
                "flower": row['花型'],
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
