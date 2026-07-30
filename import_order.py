# import_order.py
import pandas as pd
import os
import glob
import re
from datetime import datetime
from sqlalchemy import text
from mysql_conn import engine

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
    "订单成交时间": "order_time",
    "是否分期": "installment",
    "分期期数": "installment_periods",
    "手续费承担方": "fee_bearer",
    "分期方式": "installment_method",
}

# 日期时间列（需要转换）
DATETIME_COLUMNS = ["发货时间", "确认收货时间", "订单成交时间"]

# 文本列（需要去除制表符/空格）
TEXT_COLUMNS = [
    "商品", "订单号", "订单状态", "商品规格", "商家编码-规格维度",
    "商家编码-商品维度", "商家备注", "售后状态", "快递单号", "快递公司",
    "是否分期", "手续费承担方", "分期方式"
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

def get_latest_file(directory, pattern):
    """获取目录中符合模式的最新文件（按修改时间）"""
    full_pattern = os.path.join(directory, pattern)
    files = glob.glob(full_pattern)
    if not files:
        return None
    latest = max(files, key=os.path.getmtime)
    return latest

# ============================
# 主导入函数（改为 UPSERT）
# ============================
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

    # 3. 检查必要列是否存在
    required_cols = ["订单号", "商品", "商家实收金额(元)"]
    missing = [col for col in required_cols if col not in df.columns]
    if missing:
        print(f"缺少必要列: {missing}")
        print("当前列名:", df.columns.tolist())
        return

    # 4. 按字段映射重命名
    existing_cols = [col for col in COLUMN_MAPPING.keys() if col in df.columns]
    df = df[existing_cols]
    df = df.rename(columns=COLUMN_MAPPING)
    print(f"映射后保留 {len(df.columns)} 个字段")

    # 5. 数据清洗
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

    # 6. 添加默认字段（后续由其他脚本计算）
    df["cost"] = 0.0
    df["meter"] = 0.0
    df["express_cost"] = 0.0
    df["traffic_cost"] = 0.0
    df["profit"] = 0.0

    # 7. 使用 UPSERT 逐行插入（基于 order_no 唯一键）
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
                conn.execute(text(sql), params)
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
            result = conn.execute(text(sql)).fetchall()
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
    返回：{"success": bool, "message": str, "stats": dict, "changes": dict}
    """
    try:
        # 1. 检查必要列是否存在
        required_cols = ["订单号", "商品", "商家实收金额(元)"]
        missing = [col for col in required_cols if col not in df.columns]
        if missing:
            return {
                "success": False,
                "message": f"缺少必要列: {missing}",
                "stats": {},
                "changes": None,
            }

        # 2. 检查 DataFrame 是否为空
        if df.empty:
            return {
                "success": False,
                "message": "导入的数据为空",
                "stats": {},
                "changes": None,
            }

        # 3. 按字段映射重命名
        existing_cols = [col for col in COLUMN_MAPPING.keys() if col in df.columns]
        df = df[existing_cols]
        df = df.rename(columns=COLUMN_MAPPING)

        # 3. 数据清洗
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
        # 4. 添加默认字段
        df["cost"] = 0.0
        df["meter"] = 0.0
        df["express_cost"] = 0.0
        df["traffic_cost"] = 0.0
        df["profit"] = 0.0

        # 5. 统计信息
        stats = {
            "总行数": len(df),
            "字段数": len(df.columns),
            "文件来源": filename
        }

        # 6. 🔧 检测订单变化（在 UPSERT 之前对比新旧数据）
        changes = detect_order_changes(df)

        # 7. UPSERT 到数据库
        with engine.begin() as conn:
            total = 0
            for _, row in df.iterrows():
                columns = list(row.index)
                placeholders = ', '.join([f':{col}' for col in columns])
                update_pairs = ', '.join(
                    [f'{col} = VALUES({col})' for col in columns if col != 'id' and col != 'order_no'])
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
                conn.execute(text(sql), params)
                total += 1

        stats["成功导入"] = total

        return {
            "success": True,
            "message": f"成功导入 {total} 条数据到 {orders} 表",
            "stats": stats,
            "changes": changes,
        }

    except Exception as e:
        return {
            "success": False,
            "message": str(e),
            "stats": {},
            "changes": None,
        }
if __name__ == "__main__":
    # 自动查找最新文件
    # import_excel()
    import_excel(r"D:\店铺\data\7.1.xlsx")

    print("\n" + "=" * 50)
    print("下一步操作：")
    print("2. 运行 python make_daily.py    生成日报（从订单表生成）")
    print("=" * 50)