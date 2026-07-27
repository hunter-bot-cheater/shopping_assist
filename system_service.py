# system_service.py
from sqlalchemy import text
from mysql_conn import engine
from datetime import datetime, date


def get_system_start_date():
    """获取系统起始日期"""
    with engine.connect() as conn:
        result = conn.execute(
            text("SELECT config_value FROM system_config WHERE config_key = 'system_start_date'")
        ).fetchone()
        if result:
            return datetime.strptime(result[0], '%Y-%m-%d').date()
        # 默认返回 2026-07-01
        return date(2026, 7, 1)


def set_system_start_date(new_date):
    """设置系统起始日期"""
    with engine.connect() as conn:
        conn.execute(
            text("""
                INSERT INTO system_config (config_key, config_value, description)
                VALUES ('system_start_date', :val, '系统数据起始日期')
                ON DUPLICATE KEY UPDATE config_value = VALUES(config_value)
            """),
            {"val": new_date.strftime('%Y-%m-%d')}
        )
        conn.commit()
        return True


def get_date_range_for_display():
    """获取系统数据日期范围（只显示起始日期之后）"""
    start_date = get_system_start_date()
    today = datetime.now().date()

    with engine.connect() as conn:
        # 起始日期之后的订单
        earliest_order = conn.execute(
            text("""
                SELECT MIN(DATE(order_time)) 
                FROM data2026 
                WHERE order_time IS NOT NULL 
                  AND DATE(order_time) >= :start
            """),
            {"start": start_date}
        ).scalar()

        latest_order = conn.execute(
            text("""
                SELECT MAX(DATE(order_time)) 
                FROM data2026 
                WHERE order_time IS NOT NULL 
                  AND DATE(order_time) >= :start
            """),
            {"start": start_date}
        ).scalar()

        # 起始日期之后的日报
        latest_report = conn.execute(
            text("""
                SELECT MAX(report_date) 
                FROM daily_report_cache 
                WHERE report_date >= :start
            """),
            {"start": start_date}
        ).scalar()

        # 起始日期之后的快照
        latest_snapshot = conn.execute(
            text("""
                SELECT MAX(snapshot_date) 
                FROM inventory_snapshot 
                WHERE snapshot_date >= :start
            """),
            {"start": start_date}
        ).scalar()

    return {
        'start_date': start_date,
        'today': today,
        'earliest_order': earliest_order,
        'latest_order': latest_order,
        'latest_report': latest_report,
        'latest_snapshot': latest_snapshot
    }