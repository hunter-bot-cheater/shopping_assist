# import_order.py
import pandas as pd
import os
import glob
import re
from datetime import datetime

import sqlalchemy

from mysql_conn import engine
from report_date_logic import ORDER_DATE_SQL, order_date_from_row

# ============================
# 配置
# ============================
FILE_DIR = r"D:\店铺\data"       # 订单明细文件存放目录
FILE_PATTERN = r"7.*.xlsx"                  # 匹配文件名，如 7.22.xlsx
orders = 'data2026'

# 字段映射：Excel列名 -> 数据库字段名
COLUMN_MAPPING = {
    "商品": "product",
    "订单号": "order_no",
    "订单状态": "order_status",
    "商品总价(元)": "product_total",
    "邮费(元)": "postage",
    "店铺优惠折扣(元)": "shop_discount",
    "平台优惠折扣(元)": "platform_discount",
    "多多支付立减金额(元)": "ddpay_discount",
    "用户实付金额(元)": "user_payment",
    "商家实收金额(元)": "merchant_income",
    "商品数量(件)": "product_quantity",
    "发货时间": "delivery_time",
    "确认收货时间": "receive_time",
    "商品id": "product_id",
    "商品规格": "product_spec",
    "样式ID": "style_id",
    "商家编码-规格维度": "merchant_code_spec",
    "商家编码-商品维度": "merchant_code_product",
    "商家备注": "merchant_remark",
    "售后状态": "after_sale_status",
    "快递单号": "express_no",
    "快递公司": "express_company",
    "parent_order_no": "parent_order_no",
    "订单成交时间": "order_time",
    "订单付款时间": "payment_time",
    "是否分期": "installment",
    "分期期数": "installment_periods",
    "手续费承担方": "fee_bearer",
    "分期方式": "installment_method",
}

# ============================
# 平台配置（可扩展：新增平台只需加常量 + 特征列 + 列映射）
# ============================
PLATFORM_PDD, PLATFORM_TAOBAO, PLATFORM_DOUYIN = 0, 1, 2
PLATFORM_NAMES = {PLATFORM_PDD: "拼多多", PLATFORM_TAOBAO: "淘宝", PLATFORM_DOUYIN: "抖音"}

# 平台特征列（用于自动识别平台；dict 插入顺序=识别优先级）
# 注意：抖音文件含"售后状态"列（拼多多特征之一），抖音必须排在拼多多之前
PLATFORM_FEATURE_COLUMNS = {
    PLATFORM_TAOBAO: ['订单编号', '商品标题', '宝贝种类', '商品名称'],
    PLATFORM_DOUYIN: ['主订单编号', '子订单编号', '选购商品'],
    PLATFORM_PDD: ['订单号', '商品', '商家实收金额(元)', '售后状态'],
}

# 各平台 列名 -> 标准列名 映射（先转成拼多多标准列，再统一走 COLUMN_MAPPING）
PLATFORM_COLUMN_MAPPINGS = {
    PLATFORM_TAOBAO: {
        '订单编号': '订单号', '商品标题': '商品', '商品名称': '商品',
        '总金额(旧版)': '商品总价(元)', '买家应付邮费': '邮费(元)',
        '买家实付金额': '用户实付金额(元)', '买家应付货款': '商家实收金额(元)',
        '宝贝总数量': '商品数量(件)', '发货时间': '发货时间', '确认收货时间': '确认收货时间',
        '商品属性SKU': '商品规格', '物流单号': '快递单号', '物流公司': '快递公司',
        '订单创建时间': '订单成交时间', '订单状态': '订单状态', '商家备注': '商家备注',
        '退款金额': '_temp_refund_amount', '订单关闭原因': '_temp_close_reason',
    },
    PLATFORM_DOUYIN: {
        # 同一主订单的多个子订单行共用主订单编号，作为合并发货计快递费的标识
        '主订单编号': 'parent_order_no',
        # 用子订单编号作订单号：抖音一个主订单含多个子订单行，主订单编号会使
        # 同父订单多行 UPSERT 互相覆盖导致丢数据；子订单编号全文件唯一(206/206)
        '子订单编号': '订单号',
        '选购商品': '商品', '商品数量': '商品数量(件)',
        '订单应付金额': '商家实收金额(元)', '运费': '邮费(元)',
        '订单状态': '订单状态', '售后状态': '售后状态',
        '发货时间': '发货时间', '订单完成时间': '确认收货时间',
        '订单提交时间': '订单成交时间', '支付完成时间': '订单付款时间',
        '物流SN码': '快递单号',
        '商家备注': '商家备注', '取消原因': '_temp_close_reason',
    },
}

# 日期时间列（需要转换）
DATETIME_COLUMNS = ["发货时间", "确认收货时间", "订单成交时间", "订单付款时间"]

# 文本列（需要去除制表符/空格）
TEXT_COLUMNS = [
    "商品", "订单号", "订单状态", "商品规格", "商家编码-规格维度",
    "商家编码-商品维度", "商家备注", "售后状态", "快递单号", "快递公司",
    "是否分期", "手续费承担方", "分期方式", "parent_order_no"
]



# 数值列（确保转换为数字）
NUMERIC_COLUMNS = [
    "商品总价(元)", "邮费(元)", "店铺优惠折扣(元)", "平台优惠折扣(元)",
    "多多支付立减金额(元)", "用户实付金额(元)", "商家实收金额(元)",
    "商品数量(件)"
]

# ============================
# 工具函数
# ============================
def clean_text(x):
    """清理文本：转字符串、去除首尾空白、空值转None"""
    if pd.isna(x):
        return None
    x = str(x).strip()
    if x == "" or x == "nan" or x == "None":
        return None
    return x

def clean_datetime(x):
    """清理日期时间：去除制表符等非法字符，转为NaT或None"""
    if pd.isna(x):
        return None
    x = str(x).strip()
    if x == "" or x == "nan" or x == "None" or x == "\t":
        return None
    try:
        dt = pd.to_datetime(x)
        if pd.isna(dt):
            return None
        return dt
    except:
        return None

def clean_numeric(x):
    """清理数值：转为float，失败则返回None"""
    if pd.isna(x):
        return None
    try:
        return float(x)
    except:
        return None


def extract_flower_from_spec(spec):
    """从商品规格中提取花型（兼容拼多多/淘宝格式）。

    淘宝 SKU 形如「颜色分类:黑底条纹两米（长度自选）」，需先去掉前缀和米数后缀。
    """
    if pd.isna(spec):
        return None
    spec = str(spec).strip()
    if not spec:
        return None
    # 淘宝 SKU 前缀：颜色分类:XX一米（门幅1.43米）
    if '颜色分类' in spec:
        spec = re.sub(r'^.*?颜色分类[:：]?', '', spec).strip()
    # 多个 SKU 用逗号/分号分隔时取第一个（抖音规格形如「花型;二米（说明）」）
    for sep in [',', '，', ';', '；']:
        if sep in spec:
            spec = spec.split(sep)[0].strip()
            break
    # 括号前（门幅/促销说明等）
    for sep in ['（', '(']:
        if sep in spec:
            spec = spec.split(sep)[0].strip()
            break
    # 去掉米数后缀：一米/两米/三米... 以及"价格多拍连裁"等尾巴
    spec = re.sub(r'(半米|一米|两米|二米|三米|四米|五米|六米|\d+(?:\.\d+)?\s*米).*$', '', spec).strip()
    return spec if spec else None

def get_latest_file(directory, pattern):
    """获取目录中符合模式的最新文件（按修改时间）"""
    full_pattern = os.path.join(directory, pattern)
    files = glob.glob(full_pattern)
    if not files:
        return None
    latest = max(files, key=os.path.getmtime)
    return latest

def detect_platform(df):
    """根据特征列自动识别订单来源平台，返回 PLATFORM_* 常量；无匹配时默认拼多多。"""
    cols = set(df.columns)
    for platform, features in PLATFORM_FEATURE_COLUMNS.items():
        if any(f in cols for f in features):
            return platform
    return PLATFORM_PDD

def gen_taobao_after_sale_status(row):
    """淘宝无原生售后状态列，根据 订单状态/退款金额/发货收货时间 合成。"""
    order_status = str(row.get('订单状态', ''))
    refund_amount = float(row.get('_temp_refund_amount', 0) or 0)
    delivery_time = row.get('发货时间')
    receive_time = row.get('确认收货时间')
    close_reason = str(row.get('_temp_close_reason', ''))

    # 交易成功，无退款
    if order_status == '交易成功' and refund_amount == 0:
        return None

    # 交易关闭 + 退款金额 > 0
    if order_status == '交易关闭' and refund_amount > 0:
        if pd.isna(delivery_time) or delivery_time is None:
            return '未发货，退款成功'
        elif pd.isna(receive_time) or receive_time is None:
            return '已发货，退款成功'
        else:
            return '已收货，退款成功'

    # 有些退款订单订单状态可能不是"交易关闭"，但关闭原因是退款
    if '退款' in close_reason and refund_amount > 0:
        if pd.isna(delivery_time) or delivery_time is None:
            return '未发货，退款成功'
        elif pd.isna(receive_time) or receive_time is None:
            return '已发货，退款成功'
        else:
            return '已收货，退款成功'

    return None

def gen_douyin_after_sale_status(row):
    """合成抖音标准售后状态。

    抖音原生售后状态有「退款成功/售后关闭/售后待处理/补寄*」等值。
    仅当 售后状态 含"退款成功"（或取消原因含"退款"）时视为退款，
    按发货/收货时间区分 '未发货/已发货/已收货，退款成功'；其余返回 None。
    """
    after_sale = str(row.get('售后状态', '')).strip()
    close_reason = str(row.get('_temp_close_reason', ''))
    delivery_time = row.get('发货时间')
    receive_time = row.get('确认收货时间')

    if '退款成功' not in after_sale and '退款' not in close_reason:
        return None

    def _is_empty(v):
        if v is None:
            return True
        if isinstance(v, str):
            return not v.strip() or v.strip().lower() in ('nan', 'none', 'nat')
        try:
            return bool(pd.isna(v))
        except (TypeError, ValueError):
            return False

    if _is_empty(delivery_time):
        return '未发货，退款成功'
    if _is_empty(receive_time):
        return '已发货，退款成功'
    return '已收货，退款成功'

def platform_column_exists():
    """检查 data2026 表是否已有 platform 列。"""
    try:
        with engine.connect() as conn:
            row = conn.execute(
                sqlalchemy.text(f"SHOW COLUMNS FROM {orders} LIKE 'platform'")
            ).fetchone()
        return bool(row)
    except Exception:
        return False

def ensure_platform_column():
    """确保 data2026 表有 platform 列，缺失时自动添加（幂等，可安全重复调用）。
    返回 (ok, message)；ok=True 时 message 仅在本次执行了迁移时非空。"""
    if platform_column_exists():
        return True, ""
    try:
        with engine.begin() as conn:
            conn.execute(
                sqlalchemy.text(f"""
                    ALTER TABLE {orders}
                    ADD COLUMN platform TINYINT(1) NOT NULL DEFAULT 0
                    COMMENT '订单来源平台: 0=拼多多, 1=淘宝, 2=抖音(预留)' AFTER id
                """)
            )
        return True, "✅ 已自动为 data2026 表添加 platform 字段（迁移完成）"
    except Exception as e:
        return False, f"自动迁移失败，请手动运行 python migrate_platform.py：{e}"

def parent_order_no_column_exists():
    """检查 data2026 表是否已有 parent_order_no 列。"""
    try:
        with engine.connect() as conn:
            row = conn.execute(
                sqlalchemy.text(f"SHOW COLUMNS FROM {orders} LIKE 'parent_order_no'")
            ).fetchone()
        return bool(row)
    except Exception:
        return False

def ensure_parent_order_no_column():
    """确保 data2026 表有 parent_order_no 列，缺失时自动添加（幂等，可安全重复调用）。
    返回 (ok, message)；ok=True 时 message 仅在本次执行了迁移时非空。"""
    if parent_order_no_column_exists():
        return True, ""
    try:
        with engine.begin() as conn:
            conn.execute(
                sqlalchemy.text(f"""
                    ALTER TABLE {orders}
                    ADD COLUMN parent_order_no VARCHAR(50) NULL
                    COMMENT '父订单号（抖音主订单编号）' AFTER platform
                """)
            )
        return True, "✅ 已自动为 data2026 表添加 parent_order_no 字段（迁移完成）"
    except Exception as e:
        return False, f"自动迁移失败，请手动运行 python migrate_parent_order.py：{e}"

# ============================
# 主导入函数（改为 UPSERT）
# ============================
def _detect_unmatched_products(df):
    """检测导入订单中未匹配到成本表花型的商品（新商品未建档提示）。

    与日报/区间/月报同一套 assign_flowers 匹配口径；返回 {商品名: 行数}。
    仅提示，不影响导入本身。
    """
    try:
        from make_daily import assign_flowers, load_cost_map
        cost_map = load_cost_map()
        if not cost_map or df is None or df.empty or 'product' not in df.columns:
            return {}
        small = df[['product', 'product_spec']].copy()
        small = assign_flowers(small, set(cost_map.keys()))
        unmatched = small[small['花型'] == '未匹配']
        if unmatched.empty:
            return {}
        return unmatched['product'].fillna('(空商品)').value_counts().to_dict()
    except Exception as e:
        print(f"⚠️ 新商品建档检测失败：{e}")
        return {}


def import_excel(file_path=None):
    """
    导入订单明细Excel到MySQL，使用 UPSERT（重复则更新）
    若未指定file_path，则自动查找目录中最新的文件
    """
    # 1. 确定要导入的文件
    if file_path is None:
        file_path = get_latest_file(FILE_DIR, FILE_PATTERN)
        if file_path is None:
            print(f"错误：在 {FILE_DIR} 中没有找到匹配 {FILE_PATTERN} 的文件")
            return
        print(f"自动选择最新文件: {file_path}")
    else:
        if not os.path.exists(file_path):
            print(f"错误：文件不存在 - {file_path}")
            return

    # 2. 读取Excel
    print(f"正在读取文件: {file_path}")
    try:
        df = pd.read_excel(file_path, sheet_name=0)
    except Exception as e:
        print(f"读取Excel失败: {e}")
        return

    print(f"读取到 {len(df)} 行数据，{len(df.columns)} 列")

    # 3. 检测平台 + 平台列映射（淘宝/拼多多自动识别）
    platform = detect_platform(df)
    print(f"🔍 检测到订单平台: {PLATFORM_NAMES.get(platform, platform)}")
    mapping = PLATFORM_COLUMN_MAPPINGS.get(platform, {})
    if mapping:
        existing_mapping = {k: v for k, v in mapping.items() if k in df.columns}
        df = df.rename(columns=existing_mapping)
        # 多个平台列映射到同一标准列时会产生重复列，保留第一个避免取值返回 Series
        df = df.loc[:, ~df.columns.duplicated()]
        if platform == PLATFORM_TAOBAO:
            df['售后状态'] = df.apply(gen_taobao_after_sale_status, axis=1)
            df = df.drop(columns=['_temp_refund_amount', '_temp_close_reason'], errors='ignore')
            # 商品标题为空时（淘宝关闭订单导出为空），从SKU规格提取花型名兜底
            if '商品规格' in df.columns:
                empty_prod = df['商品'].isna() & df['商品规格'].notna()
                if empty_prod.any():
                    df.loc[empty_prod, '商品'] = df.loc[empty_prod, '商品规格'].apply(extract_flower_from_spec)
        elif platform == PLATFORM_DOUYIN:
            df['售后状态'] = df.apply(gen_douyin_after_sale_status, axis=1)
            df = df.drop(columns=['_temp_close_reason'], errors='ignore')

    # 4. 检查必要列是否存在（映射后统一为拼多多标准列名）
    required_cols = ["订单号", "商品", "商家实收金额(元)"]
    missing = [col for col in required_cols if col not in df.columns]
    if missing:
        print(f"缺少必要列: {missing}")
        print("当前列名:", df.columns.tolist())
        return

    # 确保 data2026 有 platform 列（缺失时自动迁移）
    ok, migrate_msg = ensure_platform_column()
    if not ok:
        print(f"错误：{migrate_msg}")
        return
    if migrate_msg:
        print(migrate_msg)

    # 确保有 parent_order_no 列（抖音主订单编号，缺失时自动迁移）
    ok2, migrate_msg2 = ensure_parent_order_no_column()
    if not ok2:
        print(f"错误：{migrate_msg2}")
        return
    if migrate_msg2:
        print(migrate_msg2)

    # 5. 按字段映射重命名
    existing_cols = [col for col in COLUMN_MAPPING.keys() if col in df.columns]
    df = df[existing_cols]
    df = df.rename(columns=COLUMN_MAPPING)
    print(f"映射后保留 {len(df.columns)} 个字段")

    # 6. 数据清洗
    for col in TEXT_COLUMNS:
        db_col = COLUMN_MAPPING.get(col)
        if db_col and db_col in df.columns:
            df[db_col] = df[db_col].apply(clean_text)

    for col in NUMERIC_COLUMNS:
        db_col = COLUMN_MAPPING.get(col)
        if db_col and db_col in df.columns:
            df[db_col] = df[db_col].apply(clean_numeric)

    for col in DATETIME_COLUMNS:
        db_col = COLUMN_MAPPING.get(col)
        if db_col and db_col in df.columns:
            df[db_col] = df[db_col].apply(clean_datetime)

    # 删除全为空的行
    df = df.dropna(how='all')
    print(f"清洗后剩余 {len(df)} 行数据")

    # 7. 添加默认字段（后续由其他脚本计算）
    df["cost"] = 0.0
    df["meter"] = 0.0
    df["express_cost"] = 0.0
    df["traffic_cost"] = 0.0
    df["profit"] = 0.0
    df["platform"] = platform

    # 8. 使用 UPSERT 逐行插入（基于 order_no 唯一键）
    from sqlalchemy.dialects.mysql import insert

    with engine.begin() as conn:
        # 获取表结构（用于构建插入语句）
        table = 'orders'  # 需先定义，或直接用 text 构造

        # 更稳健：使用 insert().on_duplicate_key_update()
        # 由于我们没有反射表对象，可以用 text 执行参数化语句
        # 也可以直接用 pd.DataFrame.to_sql 但无法处理重复，容易报错
        # 这里采用逐行执行 INSERT ... ON DUPLICATE KEY UPDATE
        # 为提高性能，批量处理（每500条一批）
        batch_size = 500
        total = 0
        for start in range(0, len(df), batch_size):
            batch = df.iloc[start:start+batch_size]
            for _, row in batch.iterrows():
                # 构建 INSERT 语句
                # 注意：字段列表需与表结构一致
                columns = list(row.index)
                # 构造参数占位符
                placeholders = ', '.join([f':{col}' for col in columns])
                # 构造 ON DUPLICATE KEY UPDATE 部分
                update_pairs = ', '.join([f'{col} = VALUES({col})' for col in columns if col != 'id' and col != 'order_no'])
                sql = f"""
                    INSERT INTO {orders} ({', '.join(columns)})
                    VALUES ({placeholders})
                    ON DUPLICATE KEY UPDATE {update_pairs}
                """
                # 将行转为字典，处理 NaN
                params = {}
                for col in columns:
                    val = row[col]
                    if pd.isna(val):
                        params[col] = None
                    else:
                        params[col] = val
                conn.execute(sqlalchemy.text(sql), params)
                total += 1
            print(f"已处理 {total} 条记录")

        print(f"成功 UPSERT {total} 条数据到 {orders} 表")

    print("导入完成！")
    # 在导入完成后，自动同步退款明细
    try:
        from populate_refund_details import sync_refund_details
        sync_refund_details()
    except Exception as e:
        print(f"⚠️ 退款明细同步失败（不影响主导入）: {e}")

        # ============================================================
        # 🆕 6. 自动生成缺失日期的日报（按下单日期统计）
        # ============================================================
        if not df.empty:
            from make_daily import generate_daily_report
            from sqlalchemy import text

            order_dates = sorted({
                d for d in (order_date_from_row(row) for _, row in df.iterrows())
                if d is not None
            })

            if len(order_dates) > 0:
                with engine.connect() as conn:
                    placeholders = ','.join(['%s'] * len(order_dates))
                    query = text(f"""
                            SELECT DISTINCT report_date 
                            FROM daily_report_cache 
                            WHERE report_date IN ({placeholders})
                        """)
                    existing_dates = conn.execute(query, order_dates).fetchall()
                    existing_date_set = {row[0] for row in existing_dates}

                missing_dates = [d for d in order_dates if d not in existing_date_set]

                if len(missing_dates) > 0:
                    print(f"🔄 发现 {len(missing_dates)} 个缺失日报的日期，自动生成...")
                    for date in missing_dates:
                        date_str = date.strftime('%Y-%m-%d')
                        try:
                            generate_daily_report(date_str, force=True)
                        except Exception as e:
                            print(f"  ⚠️ {date_str} 日报生成失败：{e}")
                    print(f"✅ 缺失日报生成完成")
                else:
                    print("ℹ️ 所有订单日期均已有日报")
            else:
                print("ℹ️ 导入数据中没有有效的下单日期")

        _pending = _detect_unmatched_products(df)
        _stats = {"总行数": len(df), "成功导入": len(df)}
        _message = f"成功导入 {len(df)} 条数据到 {orders} 表"
        if _pending:
            _stats["待建档新商品"] = _pending
            _message += f"；⚠️ {len(_pending)} 个商品未在成本表建档，将单独汇总为「待建成本」"
        return {
            "success": True,
            "message": _message,
            "stats": _stats
        }
    except Exception as e:
        return {
            "success": False,
            "message": str(e),
            "stats": {}
        }
# 为了使用 text 方式，我们需要定义表名变量，已经存在 orders
# 注意：上述方法在循环内执行多次单条插入，性能可能较慢，但数据量不大（几百条）可以接受。
# 若要更快，可使用 executemany 方式，但需构造多行 VALUES，此处暂用单条。

# 如果希望更高效，可以使用以下批量方法（但需注意参数格式），这里提供一个备选：
# 不过单条插入对几百条数据足够，暂不优化。

# 定义表对象（用于反射，但用 text 可省去）
# 为了简化，我们直接使用上面 text 方式。
# ============================
# 订单变化检测（导入前对比新旧数据）
# ============================
def detect_order_changes(df):
    """
    检测导入数据相对于数据库的变化，重点关注影响利润的变动。
    返回: dict {
        "changes": DataFrame (变化明细),
        "summary": dict (统计摘要)
    }
    """
    PROFIT_FIELDS = {
        'order_status': '订单状态',
        'after_sale_status': '售后状态',
        'merchant_income': '商家实收金额',
        'product_quantity': '商品数量',
        'product_spec': '商品规格',
        'product_total': '商品总价',
        'postage': '邮费',
        'shop_discount': '店铺优惠折扣',
        'platform_discount': '平台优惠折扣',
        'user_payment': '用户实付金额',
    }

    new_order_nos = df['order_no'].dropna().unique().tolist()
    if not new_order_nos:
        return {"changes": pd.DataFrame(), "summary": {"new_orders": 0, "changed_orders": 0, "unchanged_orders": 0, "important_changes": 0}}

    # 查询数据库中已有数据
    with engine.connect() as conn:
        existing_rows = []
        batch_size = 500
        for i in range(0, len(new_order_nos), batch_size):
            batch = new_order_nos[i:i+batch_size]
            placeholders = ','.join([f"'{o}'" for o in batch])
            sql = f"""
                SELECT order_no, order_status, after_sale_status, merchant_income,
                       product_quantity, product_spec, product_total, postage,
                       shop_discount, platform_discount, user_payment
                FROM {orders}
                WHERE order_no IN ({placeholders})
            """
            result = conn.execute(sqlalchemy.text(sql)).fetchall()
            existing_rows.extend(result)

    old_data = {}
    for row in existing_rows:
        old_data[row[0]] = {
            'order_status': row[1],
            'after_sale_status': row[2],
            'merchant_income': float(row[3]) if row[3] is not None else None,
            'product_quantity': int(row[4]) if row[4] is not None else None,
            'product_spec': row[5],
            'product_total': float(row[6]) if row[6] is not None else None,
            'postage': float(row[7]) if row[7] is not None else None,
            'shop_discount': float(row[8]) if row[8] is not None else None,
            'platform_discount': float(row[9]) if row[9] is not None else None,
            'user_payment': float(row[10]) if row[10] is not None else None,
        }

    changes_list = []
    for _, new_row in df.iterrows():
        order_no = new_row['order_no']
        if pd.isna(order_no):
            continue
        old = old_data.get(order_no)
        product_name = str(new_row.get('product', ''))[:30]

        if old is None:
            new_income = float(new_row.get('merchant_income', 0) or 0)
            changes_list.append({
                '订单号': order_no,
                '商品': product_name,
                '变化类型': '🆕 新订单',
                '变化字段': '-',
                '旧值': '-',
                '新值': '-',
                '利润影响': f'+{new_income:.2f} 元' if new_income > 0 else '无',
                '重要程度': '🟢 普通' if new_income > 0 else '⚪ 无影响',
            })
            continue

        for field_key, field_label in PROFIT_FIELDS.items():
            old_val = old.get(field_key)
            new_val_raw = new_row.get(field_key)

            if pd.isna(new_val_raw):
                new_val = None
            elif field_key in ('merchant_income', 'product_total', 'postage',
                               'shop_discount', 'platform_discount', 'user_payment'):
                try:
                    new_val = float(new_val_raw)
                except (ValueError, TypeError):
                    new_val = None
            elif field_key == 'product_quantity':
                try:
                    new_val = int(float(new_val_raw))
                except (ValueError, TypeError):
                    new_val = None
            else:
                new_val = str(new_val_raw) if new_val_raw is not None else None

            if old_val == new_val:
                continue

            # 计算利润影响
            profit_impact = _calc_profit_impact(field_key, old_val, new_val, old, new_row)

            if field_key in ('order_status', 'after_sale_status', 'merchant_income'):
                importance = '🔴 重要'
            elif field_key in ('product_quantity', 'product_spec'):
                importance = '🟡 关注'
            else:
                importance = '🟢 普通'

            old_display = str(old_val)[:50] if old_val is not None else '（空）'
            if isinstance(old_val, float):
                old_display = f'{old_val:.2f}'

            new_display = str(new_val)[:50] if new_val is not None else '（空）'
            if isinstance(new_val, float):
                new_display = f'{new_val:.2f}'

            changes_list.append({
                '订单号': order_no,
                '商品': product_name,
                '变化类型': '📝 变更',
                '变化字段': field_label,
                '旧值': old_display,
                '新值': new_display,
                '利润影响': profit_impact,
                '重要程度': importance,
            })

    changes_df = pd.DataFrame(changes_list) if changes_list else pd.DataFrame()

    if not changes_df.empty:
        new_count = len(changes_df[changes_df['变化类型'] == '🆕 新订单']['订单号'].unique())
        changed_orders = changes_df[changes_df['变化类型'] == '📝 变更']['订单号'].nunique()
        important = len(changes_df[changes_df['重要程度'] == '🔴 重要'])
    else:
        new_count = len(new_order_nos) - len(old_data)
        changed_orders = 0
        important = 0

    return {
        "changes": changes_df,
        "summary": {
            "new_orders": new_count,
            "changed_orders": changed_orders,
            "unchanged_orders": len(old_data) - changed_orders,
            "important_changes": important,
        }
    }


def _calc_profit_impact(field_key, old_val, new_val, old_data, new_row):
    """计算字段变化对利润的影响"""
    if field_key in ('order_status', 'after_sale_status'):
        old_str = str(old_val) if old_val else ''
        new_str = str(new_val) if new_val else ''
        if ('取消' in new_str or '退款成功' in new_str) and ('取消' not in old_str and '退款成功' not in old_str):
            income = old_data.get('merchant_income') or 0
            return f'🔻 -{float(income):.2f} 元（收入损失）'
        if ('取消' in old_str or '退款成功' in old_str) and ('取消' not in new_str and '退款成功' not in new_str):
            new_income = float(new_row.get('merchant_income', 0) or 0)
            return f'🔺 +{new_income:.2f} 元（恢复）'

    if field_key == 'merchant_income':
        old_v = float(old_val) if old_val else 0
        new_v = float(new_val) if new_val else 0
        diff = new_v - old_v
        return f'{"🔺" if diff > 0 else "🔻"} {diff:+.2f} 元' if abs(diff) >= 0.01 else '无'

    if field_key == 'product_quantity':
        old_q = int(float(old_val)) if old_val is not None else 0
        new_q = int(float(new_val)) if new_val is not None else 0
        diff = new_q - old_q
        return f'📦 {"+" if diff > 0 else ""}{diff} 件' if diff != 0 else '无'

    if field_key == 'product_spec':
        return '⚠️ 规格变化影响花型/米数匹配'

    if field_key in ('product_total', 'user_payment'):
        old_v = float(old_val) if old_val else 0
        new_v = float(new_val) if new_val else 0
        diff = new_v - old_v
        return f'{"🔺" if diff > 0 else "🔻"} {diff:+.2f} 元' if abs(diff) >= 0.01 else '无'

    return '-'


# ============================
# 网页上传导入（直接接收 DataFrame）
# ============================
def import_excel_from_dataframe(df, filename="web_upload.xlsx"):
    """
    从 DataFrame 导入数据（用于网页上传）
    支持拼多多和淘宝订单格式（自动检测）
    返回：{"success": bool, "message": str, "stats": dict, "changes": dict}
    """
    try:
        # ============================================================
        # 🔧 第一步：检测平台类型（淘宝/拼多多），进行列映射
        # ============================================================
        platform = detect_platform(df)
        print(f"🔍 检测到订单平台: {PLATFORM_NAMES.get(platform, platform)}")

        mapping = PLATFORM_COLUMN_MAPPINGS.get(platform, {})
        if mapping:
            # 只保留存在的列进行重命名（平台列名 -> 标准列名）
            existing_mapping = {k: v for k, v in mapping.items() if k in df.columns}
            df = df.rename(columns=existing_mapping)
            # 多个平台列可能映射到同一标准列（如 商品标题/商品名称 → 商品），
            # 会产生重复列，导致后续 row[col] 返回 Series 报错；保留第一个即可
            df = df.loc[:, ~df.columns.duplicated()]

        # 淘宝无原生售后状态列，需根据订单状态/退款金额/发货收货时间合成
        if platform == PLATFORM_TAOBAO:
            df['售后状态'] = df.apply(gen_taobao_after_sale_status, axis=1)
            # 删除临时列
            df = df.drop(columns=['_temp_refund_amount', '_temp_close_reason'], errors='ignore')
            # 商品标题为空时（淘宝关闭订单导出为空），从SKU规格提取花型名兜底
            if '商品规格' in df.columns:
                empty_prod = df['商品'].isna() & df['商品规格'].notna()
                if empty_prod.any():
                    df.loc[empty_prod, '商品'] = df.loc[empty_prod, '商品规格'].apply(extract_flower_from_spec)
            print(f"✅ 淘宝格式映射完成，共 {len(df)} 行")
        elif platform == PLATFORM_DOUYIN:
            # 抖音原生售后状态是「退款成功/售后关闭/补寄*」等，需合成标准状态
            df['售后状态'] = df.apply(gen_douyin_after_sale_status, axis=1)
            df = df.drop(columns=['_temp_close_reason'], errors='ignore')
            print(f"✅ 抖音格式映射完成，共 {len(df)} 行")
        else:
            print("🔍 拼多多格式直接处理")

        # ============================================================
        # 第二步：检查必要列是否存在
        # ============================================================
        required_cols = ["订单号", "商品", "商家实收金额(元)"]
        missing = [col for col in required_cols if col not in df.columns]
        if missing:
            return {
                "success": False,
                "message": f"缺少必要列: {missing}",
                "stats": {},
                "changes": None,
            }

        # ============================================================
        # 第三步：检查 DataFrame 是否为空
        # ============================================================
        if df.empty:
            return {
                "success": False,
                "message": "导入的数据为空",
                "stats": {},
                "changes": None,
            }

        # 确保 data2026 有 platform 列（缺失时自动迁移）
        ok, migrate_msg = ensure_platform_column()
        if not ok:
            return {
                "success": False,
                "message": migrate_msg,
                "stats": {},
                "changes": None,
            }
        if migrate_msg:
            print(migrate_msg)

        # 确保有 parent_order_no 列（抖音主订单编号，缺失时自动迁移）
        ok2, migrate_msg2 = ensure_parent_order_no_column()
        if not ok2:
            return {
                "success": False,
                "message": migrate_msg2,
                "stats": {},
                "changes": None,
            }
        if migrate_msg2:
            print(migrate_msg2)

        # ============================================================
        # 第四步：按字段映射重命名（标准列名 -> 数据库字段名）
        # ============================================================
        existing_cols = [col for col in COLUMN_MAPPING.keys() if col in df.columns]
        df = df[existing_cols]
        df = df.rename(columns=COLUMN_MAPPING)

        # ============================================================
        # 第五步：数据清洗
        # ============================================================
        for col in TEXT_COLUMNS:
            db_col = COLUMN_MAPPING.get(col)
            if db_col and db_col in df.columns:
                df[db_col] = df[db_col].apply(clean_text)

        for col in NUMERIC_COLUMNS:
            db_col = COLUMN_MAPPING.get(col)
            if db_col and db_col in df.columns:
                df[db_col] = df[db_col].apply(clean_numeric)

        for col in DATETIME_COLUMNS:
            db_col = COLUMN_MAPPING.get(col)
            if db_col and db_col in df.columns:
                df[db_col] = df[db_col].apply(clean_datetime)

        # 删除全为空的行
        df = df.dropna(how='all')

        # 统一花型名称（将白底黑色条纹合并到白底乱纹）
        if 'product_spec' in df.columns:
            df['product_spec'] = df['product_spec'].astype(str).str.replace('白底黑色条纹', '白底乱纹', regex=False)

        # ============================================================
        # 第六步：添加默认字段
        # ============================================================
        df["cost"] = 0.0
        df["meter"] = 0.0
        df["express_cost"] = 0.0
        df["traffic_cost"] = 0.0
        df["profit"] = 0.0
        df["platform"] = platform

        # ============================================================
        # 第七步：统计信息
        # ============================================================
        stats = {
            "总行数": len(df),
            "字段数": len(df.columns),
            "文件来源": filename
        }

        # ============================================================
        # 第八步：检测订单变化（在 UPSERT 之前对比新旧数据）
        # ============================================================
        changes = detect_order_changes(df)

        # ============================================================
        # 第九步：UPSERT 到数据库
        # ============================================================
        with engine.begin() as conn:
            total = 0
            for _, row in df.iterrows():
                columns = list(row.index)
                placeholders = ', '.join([f':{col}' for col in columns])
                update_pairs = ', '.join(
                    [f'{col} = VALUES({col})' for col in columns if col != 'id' and col != 'order_no']
                )
                sql = f"""
                    INSERT INTO {orders} ({', '.join(columns)})
                    VALUES ({placeholders})
                    ON DUPLICATE KEY UPDATE {update_pairs}
                """
                params = {}
                for col in columns:
                    val = row[col]
                    if pd.isna(val):
                        params[col] = None
                    else:
                        params[col] = val
                conn.execute(sqlalchemy.text(sql), params)
                total += 1

        stats["成功导入"] = total

        # ============================================================
        # 第十步：收集受影响日期（有变化订单的下单日期，日报按下单日期统计）
        # ============================================================
        affected_dates = set()
        changes_df = changes.get("changes")
        if changes_df is not None and not changes_df.empty and '订单号' in changes_df.columns:
            order_nos = changes_df['订单号'].dropna().unique().tolist()
            if order_nos:
                with engine.connect() as conn:
                    placeholders = ', '.join([f':o{i}' for i in range(len(order_nos))])
                    params = {f'o{i}': on for i, on in enumerate(order_nos)}
                    query = sqlalchemy.text(f"""
                        SELECT DISTINCT order_date AS affected_date
                        FROM (
                            SELECT {ORDER_DATE_SQL} AS order_date
                            FROM {orders}
                            WHERE order_no IN ({placeholders})
                        ) t
                        WHERE order_date IS NOT NULL
                    """)
                    rows = conn.execute(query, params).fetchall()
                    for row in rows:
                        d = row[0]
                        affected_dates.add(d.strftime('%Y-%m-%d') if hasattr(d, 'strftime') else str(d))

        _pending = _detect_unmatched_products(df)
        _message = f"成功导入 {total} 条数据到 {orders} 表"
        if _pending:
            stats["待建档新商品"] = _pending
            _message += f"；⚠️ {len(_pending)} 个商品未在成本表建档，将单独汇总为「待建成本」"
        return {
            "success": True,
            "message": _message,
            "stats": stats,
            "changes": changes,
            "affected_dates": sorted(affected_dates),
        }

    except Exception as e:
        return {
            "success": False,
            "message": str(e),
            "stats": {},
            "changes": None,
            "affected_dates": [],
        }
if __name__ == "__main__":
    # 自动查找最新文件
    # import_excel()
    import_excel(r"D:\店铺\data\7.1.xlsx")

    print("\n" + "=" * 50)
    print("下一步操作：")
    print("2. 运行 python make_daily.py    生成日报（从订单表生成）")
    print("=" * 50)