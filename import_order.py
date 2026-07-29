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
# 网页上传导入（直接接收 DataFrame）
# ============================
def import_excel_from_dataframe(df, filename="web_upload.xlsx"):
    """
    从 DataFrame 导入数据（用于网页上传）
    返回：{"success": bool, "message": str, "stats": dict}
    """
    try:
        # 1. 检查必要列是否存在
        required_cols = ["订单号", "商品", "商家实收金额(元)"]
        missing = [col for col in required_cols if col not in df.columns]
        if missing:
            return {
                "success": False,
                "message": f"缺少必要列: {missing}",
                "stats": {}
            }

        # 2. 检查 DataFrame 是否为空
        if df.empty:
            return {
                "success": False,
                "message": "导入的数据为空",
                "stats": {}
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

        # 6. UPSERT 到数据库
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
            "stats": stats
        }

    except Exception as e:
        return {
            "success": False,
            "message": str(e),
            "stats": {}
        }
if __name__ == "__main__":
    # 自动查找最新文件
    # import_excel()
    import_excel(r"D:\店铺\data\7.1.xlsx")

    print("\n" + "=" * 50)
    print("下一步操作：")
    print("2. 运行 python make_daily.py    生成日报（从订单表生成）")
    print("=" * 50)