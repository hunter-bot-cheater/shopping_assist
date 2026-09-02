


## 2. schema.sql（数据库建表脚本）

    -- 先创建数据库（不存在则新建）
CREATE DATABASE IF NOT EXISTS shop_data DEFAULT CHARACTER SET utf8mb4;
-- 选中这个数据库，后续所有表都建在里面
USE shop_data;

-- ============================================================
-- 布料店库存管理系统 - 数据库建表脚本
-- 数据库名称：shop_data
-- 字符集：utf8mb4
-- ============================================================

-- 1. 订单主表
CREATE TABLE IF NOT EXISTS data2026 (
    id INT AUTO_INCREMENT PRIMARY KEY,
    platform TINYINT(1) NOT NULL DEFAULT 0 COMMENT '订单来源平台: 0=拼多多, 1=淘宝, 2=抖音(预留)',
    parent_order_no VARCHAR(50) NULL COMMENT '父订单号（抖音主订单编号）',
    product VARCHAR(255),
    order_no VARCHAR(50) UNIQUE,
    order_status VARCHAR(20),
    product_total DECIMAL(10,2),
    postage DECIMAL(10,2),
    shop_discount DECIMAL(10,2),
    platform_discount DECIMAL(10,2),
    ddpay_discount DECIMAL(10,2),
    user_payment DECIMAL(10,2),
    merchant_income DECIMAL(10,2),
    product_quantity INT,
    delivery_time DATETIME,
    receive_time DATETIME,
    product_id VARCHAR(50),
    product_spec TEXT,
    style_id VARCHAR(50),
    merchant_code_spec VARCHAR(50),
    merchant_code_product VARCHAR(50),
    merchant_remark VARCHAR(255),
    after_sale_status VARCHAR(50),
    express_no VARCHAR(50),
    express_company VARCHAR(50),
    order_time DATETIME,
    payment_time DATETIME NULL COMMENT '订单付款时间/支付完成时间（淘宝/抖音下单日期口径，导出列「订单付款时间」「支付完成时间」）',
    installment VARCHAR(10),
    installment_periods INT,
    fee_bearer VARCHAR(20),
    installment_method VARCHAR(20),
    cost DECIMAL(10,2) DEFAULT 0,
    meter DECIMAL(10,2) DEFAULT 0,
    express_cost DECIMAL(10,2) DEFAULT 0,
    traffic_cost DECIMAL(10,2) DEFAULT 0,
    profit DECIMAL(10,2) DEFAULT 0
);

-- 2. 花型成本表
CREATE TABLE IF NOT EXISTS product_cost (
    flower VARCHAR(100) PRIMARY KEY,
    cost_per_meter DECIMAL(10,4) NOT NULL DEFAULT 0,
    update_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
);

-- 3. 库存主表（预警配置）
CREATE TABLE IF NOT EXISTS inventory (
    flower VARCHAR(100) PRIMARY KEY,
    current_stock DECIMAL(10,2) DEFAULT 0,
    alert_days INT DEFAULT 7,
    supplier_lead_time INT DEFAULT 3,
    last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
);

-- 4. 库存流水表（历史变动记录）
CREATE TABLE IF NOT EXISTS inventory_log (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    flower VARCHAR(100),
    change_type ENUM('初始化','入库','销售出库','报损','盘点调整','手动调整'),
    change_qty DECIMAL(10,2),
    before_stock DECIMAL(10,2),
    after_stock DECIMAL(10,2),
    reference VARCHAR(255),
    operator VARCHAR(50),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    effect_date DATE NULL COMMENT '生效日期：该操作实际作用的库存日期；NULL 时按 created_at 的日期计'
);

-- 5. 日报缓存表
CREATE TABLE IF NOT EXISTS daily_report_cache (
    id INT AUTO_INCREMENT PRIMARY KEY,
    report_date DATE,
    flower VARCHAR(100),
    order_count INT,
    total_meters DECIMAL(10,2),
    revenue DECIMAL(10,2),
    cost DECIMAL(10,2),
    express_fee DECIMAL(10,2),
    profit DECIMAL(10,2),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY unique_daily_flower (report_date, flower)
);

-- 6. 导入历史表
CREATE TABLE IF NOT EXISTS import_history (
    id INT AUTO_INCREMENT PRIMARY KEY,
    file_name VARCHAR(255) UNIQUE,
    record_count INT,
    import_time DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- 7. 退款明细表
CREATE TABLE IF NOT EXISTS refund_detail (
    id INT AUTO_INCREMENT PRIMARY KEY,
    order_no VARCHAR(50) NOT NULL,
    flower VARCHAR(100),
    product_spec TEXT,
    product_quantity INT DEFAULT 1,
    refund_meters DECIMAL(10,2) DEFAULT 0,
    refund_amount DECIMAL(10,2) DEFAULT 0,
    after_sale_status VARCHAR(50),
    refund_time DATETIME,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE KEY uk_order_flower (order_no, flower)
);

-- 8. 日报元数据表（用于变化检测）
CREATE TABLE IF NOT EXISTS daily_report_meta (
    report_date DATE PRIMARY KEY,
    data_hash VARCHAR(64) NOT NULL,
    order_count INT DEFAULT 0,
    total_meters DECIMAL(10,2) DEFAULT 0,
    last_generated TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    status ENUM('stable','changed') DEFAULT 'stable'
);

-- 9. 系统配置表
CREATE TABLE IF NOT EXISTS system_config (
    config_key VARCHAR(50) PRIMARY KEY,
    config_value VARCHAR(255) NOT NULL,
    description VARCHAR(255),
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
);

-- 插入默认起始日期
INSERT INTO system_config (config_key, config_value, description)
VALUES ('system_start_date', '2026-07-01', '系统数据起始日期')
ON DUPLICATE KEY UPDATE config_value = VALUES(config_value);

-- 10. 库存基准表
CREATE TABLE IF NOT EXISTS inventory_base (
    flower VARCHAR(100) PRIMARY KEY,
    base_stock DECIMAL(10,2) NOT NULL DEFAULT 0,
    base_date DATE NOT NULL,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
);

-- 11. 库存快照表
CREATE TABLE IF NOT EXISTS inventory_snapshot (
    id INT AUTO_INCREMENT PRIMARY KEY,
    flower VARCHAR(100) NOT NULL,
    snapshot_date DATE NOT NULL,
    stock DECIMAL(10,2) NOT NULL DEFAULT 0,
    is_manual TINYINT(1) NOT NULL DEFAULT 0 COMMENT '是否为手动调整锚点：0-自动计算，1-手动调整（锚点，库存值固定）',
    updated_by VARCHAR(50) DEFAULT 'system',
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE KEY uk_flower_date (flower, snapshot_date)
);

-- 12. 库存变更日志
CREATE TABLE IF NOT EXISTS inventory_change_log (
    id INT AUTO_INCREMENT PRIMARY KEY,
    flower VARCHAR(100) NOT NULL,
    change_date DATE NOT NULL,
    old_stock DECIMAL(10,2) NOT NULL,
    new_stock DECIMAL(10,2) NOT NULL,
    reason VARCHAR(255),
    operator VARCHAR(50),
    changed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 13. 缺口表（保留用于兼容，但系统不再写入，可忽略）
CREATE TABLE IF NOT EXISTS inventory_shortfall (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    flower VARCHAR(100),
    shortfall_meters DECIMAL(10,2),
    reference_date DATE,
    status ENUM('待补录','已补录') DEFAULT '待补录',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    resolved_at TIMESTAMP NULL
);

-- 索引建议（可选）
CREATE INDEX idx_order_time ON data2026(order_time);
CREATE INDEX idx_report_date ON daily_report_cache(report_date);
CREATE INDEX idx_snapshot_date ON inventory_snapshot(snapshot_date);
CREATE INDEX idx_flower_date ON inventory_snapshot(flower, snapshot_date);

-- 增加软删除字段(增删花型)
ALTER TABLE product_cost
ADD COLUMN is_deleted TINYINT(1) DEFAULT 0 COMMENT '是否删除：0-正常，1-已删除',
ADD COLUMN delete_time DATETIME DEFAULT NULL COMMENT '删除时间';

ALTER TABLE product_cost ADD UNIQUE KEY uk_flower (flower);

truncate table inventory_log;

ALTER TABLE inventory_log
MODIFY COLUMN change_type ENUM(
    '初始化',
    '入库',
    '销售出库',
    '报损',
    '盘点调整',
    '手动调整',
    '新增花型',
    '删除花型',
    '恢复花型'
) NULL DEFAULT NULL;

ALTER TABLE product_cost
ADD COLUMN delete_effect_date DATE DEFAULT NULL
COMMENT '删除生效日期，该日期及之后业务隐藏该花型';

SHOW FULL COLUMNS FROM inventory_log WHERE Field = 'change_type';
# SELECT
#     flower,
#     cost_per_meter,
#     is_deleted,
#     delete_time,
#     delete_effect_date,
#     CASE
#         WHEN is_deleted = 0 THEN '正常'
#         WHEN delete_effect_date > CURDATE() THEN '待生效（次日生效）'
#         ELSE '已删除'
#     END AS status
# FROM product_cost
# ORDER BY is_deleted, flower;

# USE shop_data;
#
# -- 1. 查看 product_cost 表是否有数据
# SELECT COUNT(*) AS total_count FROM product_cost;
#
# -- 2. 查看具体数据
# SELECT flower, cost_per_meter, is_deleted, delete_effect_date
# FROM product_cost
# ORDER BY flower;
#
# -- 3. 查看是否有花型被标记为删除
# SELECT flower, is_deleted, delete_effect_date
# FROM product_cost
# WHERE is_deleted = 1;