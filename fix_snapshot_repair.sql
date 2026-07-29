-- ============================================================
-- 库存快照修复SQL
-- 问题：rollback_daily_sales 未回退 inventory_snapshot，
--       且 fill_missing_snapshots 的 ON DUPLICATE KEY UPDATE 覆盖了已有快照
-- 修复范围：黑底白色波点 2026-07-23 至最新
-- ============================================================

-- ============ 第1步：备份当前快照（万一需要回滚） ============
DROP TABLE IF EXISTS _snapshot_backup;
CREATE TABLE _snapshot_backup AS
SELECT flower, snapshot_date, stock, updated_by, updated_at
FROM inventory_snapshot
WHERE flower = '黑底白色波点'
  AND snapshot_date >= '2026-07-22'
ORDER BY snapshot_date;

-- ============ 第2步：查看当前异常数据 ============
SELECT '[当前快照]' AS info, snapshot_date, stock
FROM inventory_snapshot
WHERE flower = '黑底白色波点'
  AND snapshot_date BETWEEN '2026-07-22' AND '2026-07-27'
ORDER BY snapshot_date;

-- ============ 第3步：查看日报销售数据 ============
SELECT '[日报销售]' AS info, report_date, total_meters
FROM daily_report_cache
WHERE flower = '黑底白色波点'
  AND report_date BETWEEN '2026-07-22' AND '2026-07-27'
ORDER BY report_date;

-- ============ 第4步：查看期间入库/调整记录 ============
SELECT '[库存变动]' AS info,
       DATE(created_at) AS log_date,
       change_type,
       change_qty,
       before_stock,
       after_stock,
       reference
FROM inventory_log
WHERE flower = '黑底白色波点'
  AND DATE(created_at) BETWEEN '2026-07-22' AND '2026-07-27'
ORDER BY created_at;

-- ============ 第5步：执行修复（使用8.0+递归CTE逐日推算） ============
UPDATE inventory_snapshot s
INNER JOIN (
    WITH RECURSIVE daily_calc AS (
        -- 基准：7月22日（已知正确值139）
        SELECT
            '2026-07-22' AS calc_date,
            139.0 AS correct_stock

        UNION ALL

        -- 逐日推算
        SELECT
            DATE_ADD(c.calc_date, INTERVAL 1 DAY),
            GREATEST(
                c.correct_stock
                - COALESCE(r.total_meters, 0)
                + COALESCE(i.inbound_qty, 0)
                + COALESCE(a.adjust_qty, 0),
                0
            )
        FROM daily_calc c
        LEFT JOIN daily_report_cache r
            ON r.report_date = DATE_ADD(c.calc_date, INTERVAL 1 DAY)
            AND r.flower = '黑底白色波点'
        LEFT JOIN (
            SELECT DATE(created_at) AS d, SUM(change_qty) AS inbound_qty
            FROM inventory_log
            WHERE flower = '黑底白色波点' AND change_type = '入库'
            GROUP BY DATE(created_at)
        ) i ON i.d = DATE_ADD(c.calc_date, INTERVAL 1 DAY)
        LEFT JOIN (
            SELECT DATE(created_at) AS d, SUM(change_qty) AS adjust_qty
            FROM inventory_log
            WHERE flower = '黑底白色波点' AND change_type = '手动调整'
            GROUP BY DATE(created_at)
        ) a ON a.d = DATE_ADD(c.calc_date, INTERVAL 1 DAY)
        WHERE DATE_ADD(c.calc_date, INTERVAL 1 DAY) <= CURDATE()
    )
    SELECT calc_date, correct_stock
    FROM daily_calc
    WHERE calc_date >= '2026-07-23'
) correct ON s.flower = '黑底白色波点' AND s.snapshot_date = correct.calc_date
SET s.stock = correct.correct_stock,
    s.updated_by = 'system_repair',
    s.updated_at = CURRENT_TIMESTAMP
WHERE s.stock != correct.correct_stock;

-- ============ 第6步：验证修复结果 ============
SELECT '[修复后快照]' AS info, snapshot_date, stock,
       CASE
           WHEN stock = correct_stock THEN '✅'
           ELSE '❌'
       END AS status
FROM inventory_snapshot s
LEFT JOIN (
    WITH RECURSIVE daily_calc AS (
        SELECT '2026-07-22' AS calc_date, 139.0 AS correct_stock
        UNION ALL
        SELECT DATE_ADD(c.calc_date, INTERVAL 1 DAY),
               GREATEST(c.correct_stock - COALESCE(r.total_meters, 0) + COALESCE(i.inbound_qty, 0) + COALESCE(a.adjust_qty, 0), 0)
        FROM daily_calc c
        LEFT JOIN daily_report_cache r ON r.report_date = DATE_ADD(c.calc_date, INTERVAL 1 DAY) AND r.flower = '黑底白色波点'
        LEFT JOIN (SELECT DATE(created_at) AS d, SUM(change_qty) AS inbound_qty FROM inventory_log WHERE flower = '黑底白色波点' AND change_type = '入库' GROUP BY DATE(created_at)) i ON i.d = DATE_ADD(c.calc_date, INTERVAL 1 DAY)
        LEFT JOIN (SELECT DATE(created_at) AS d, SUM(change_qty) AS adjust_qty FROM inventory_log WHERE flower = '黑底白色波点' AND change_type = '手动调整' GROUP BY DATE(created_at)) a ON a.d = DATE_ADD(c.calc_date, INTERVAL 1 DAY)
        WHERE DATE_ADD(c.calc_date, INTERVAL 1 DAY) <= CURDATE()
    )
    SELECT calc_date, correct_stock FROM daily_calc
) t ON s.snapshot_date = t.calc_date
WHERE s.flower = '黑底白色波点'
  AND s.snapshot_date BETWEEN '2026-07-22' AND '2026-07-27'
ORDER BY s.snapshot_date;

-- ============ 第7步：检查所有花型的一致性 ============
-- 逐日验证 formula: current_snapshot ≈ (prev_day_snapshot - sales + inbound)
SELECT '[一致性检查]' AS info,
       s1.snapshot_date,
       s1.flower,
       s1.stock AS current_stock,
       s2.stock AS prev_stock,
       COALESCE(r.total_meters, 0) AS daily_sales,
       s2.stock - COALESCE(r.total_meters, 0) AS expected_before_inbound,
       s1.stock - (s2.stock - COALESCE(r.total_meters, 0)) AS offset
FROM inventory_snapshot s1
JOIN inventory_snapshot s2
    ON s1.flower = s2.flower
    AND s1.snapshot_date = DATE_ADD(s2.snapshot_date, INTERVAL 1 DAY)
LEFT JOIN daily_report_cache r
    ON r.report_date = s1.snapshot_date AND r.flower = s1.flower
WHERE s1.flower = '黑底白色波点'
  AND s1.snapshot_date BETWEEN '2026-07-23' AND '2026-07-27'
ORDER BY s1.snapshot_date;

-- ============ 第8步：清理临时表 ============
DROP TABLE IF EXISTS _snapshot_backup;
