# report_date_logic.py
# ============================================================
# 报表日期口径：按「用户下单日期」统计（替代原「发货时间」口径）
#
#   拼多多(platform=0) = 订单号前 6 位（YYMMDD，如 260724 → 2026-07-24）
#   淘宝(platform=1)   = 订单付款时间 payment_time（即导出列「订单付款时间」）
#                        缺失（未付款/历史数据）时回退到下单时间 order_time
#   抖音(platform=2)   = 支付完成时间 payment_time（即导出列「支付完成时间」）
#                        缺失时回退到订单提交时间 order_time（实测支付日期=提交日期）
#
# 本模块同时提供 SQL 片段（ORDER_DATE_SQL，供查询按日过滤）与 Python 函数
# （order_date_from_row，供导入后按行计算日期），两者口径必须保持一致。
# ============================================================
from datetime import datetime

# 各平台下单日期计算的 SQL 表达式（在 data2026 表上使用，需含 platform/order_no/
# payment_time/order_time/receive_time 列）。拼多多默认走 ELSE 分支。
ORDER_DATE_SQL = """
    CASE
        WHEN platform = 2 THEN
            CASE
                WHEN payment_time IS NOT NULL THEN DATE(payment_time)
                ELSE DATE(order_time)
            END
        WHEN platform = 1 THEN
            CASE
                WHEN payment_time IS NOT NULL THEN DATE(payment_time)
                ELSE DATE(order_time)
            END
        ELSE STR_TO_DATE(LEFT(order_no, 6), '%y%m%d')
    END
"""


def _is_na(v):
    """判断空值（兼容 None / NaN / NaT）"""
    if v is None:
        return True
    try:
        import pandas as pd
        return bool(pd.isna(v))
    except Exception:
        return False


def order_date_from_row(row):
    """按平台计算下单日期（与 ORDER_DATE_SQL 口径一致）。

    row 为 dict / pandas Series，需含 platform / order_no / payment_time /
    order_time 字段；返回 date 或 None。
    """
    platform = row.get('platform')
    # 抖音 / 淘宝：支付完成时间（订单付款时间）；缺失回退到订单提交/下单时间
    if platform in (1, 2):
        dt = row.get('payment_time')
        if dt is not None and not _is_na(dt):
            return dt.date() if hasattr(dt, 'date') else None
        dt = row.get('order_time')
        if dt is None or _is_na(dt):
            return None
        return dt.date() if hasattr(dt, 'date') else None
    # 拼多多（及默认）：订单号前 6 位（YYMMDD）
    s = row.get('order_no')
    if s is None or _is_na(s):
        return None
    s = str(s).strip()
    if len(s) >= 6 and s[:6].isdigit():
        try:
            return datetime.strptime(s[:6], '%y%m%d').date()
        except ValueError:
            return None
    return None
