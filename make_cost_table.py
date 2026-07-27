# extract_flowers.py
import pandas as pd
import os
import re
from mysql_conn import engine

# ============================
# 配置
# ============================
FILE_PATH = r"D:\店铺\b514ce656c2a652a3522128abb4224daorders_export2026-07-24-18-57-20.xlsx"
OUTPUT_TABLE = "product_cost"  # 也可以只输出到Excel

# ============================
# 提取函数
# ============================
def extract_flower_from_spec(spec):
    """从商品规格中提取花型（逗号前部分）"""
    if pd.isna(spec):
        return None
    spec = str(spec).strip()
    for sep in [',', '，']:
        if sep in spec:
            flower = spec.split(sep)[0].strip()
            return flower if flower else None
    return spec.strip() if spec else None

# ============================
# 主函数
# ============================
def extract_flowers():
    """从订单明细提取所有花型"""
    print("=" * 60)
    print("开始提取花型列表...")
    print("=" * 60)

    # 1. 检查文件是否存在
    if not os.path.exists(FILE_PATH):
        print(f"错误：文件不存在 - {FILE_PATH}")
        return

    # 2. 读取Excel
    print(f"正在读取: {FILE_PATH}")
    try:
        df = pd.read_excel(FILE_PATH, engine='openpyxl')
    except Exception as e:
        print(f"读取失败: {e}")
        return

    print(f"读取到 {len(df)} 行数据，列名: {df.columns.tolist()}")

    # 3. 检查是否有"商品规格"列
    if '商品规格' not in df.columns:
        print("错误：Excel中缺少'商品规格'列")
        return

    # 4. 提取花型
    df['花型'] = df['商品规格'].apply(extract_flower_from_spec)
    df = df.dropna(subset=['花型'])

    # 5. 去重并排序
    flowers = df['花型'].unique()
    flowers = sorted(flowers)

    print(f"\n共提取到 {len(flowers)} 个不同的花型")

    # 6. 显示前20个
    print("\n花型列表（前20个）:")
    for i, f in enumerate(flowers[:20], 1):
        print(f"  {i}. {f}")

    if len(flowers) > 20:
        print(f"  ... 还有 {len(flowers) - 20} 个")

    # 7. 保存到DataFrame
    result = pd.DataFrame({
        'flower': flowers,
        'cost_per_meter': 0.0,   # 暂填0，后续补充
        'update_time': pd.Timestamp.now()
    })

    # 8. 保存到Excel（便于查看）
    output_excel = r"D:\店铺\花型列表.xlsx"
    result.to_excel(output_excel, index=False)
    print(f"\n已保存到Excel: {output_excel}")

    # 9. 写入数据库（可选）
    try:
        result.to_sql(
            "product_cost",
            engine,
            if_exists="replace",
            index=False
        )
        print(f"已写入数据库 product_cost 表（cost_per_meter 为 0，待补充）")
    except Exception as e:
        print(f"写入数据库失败: {e}")

    print("\n" + "=" * 60)
    print("花型列表提取完成！")
    print("=" * 60)

    # 返回花型列表，方便后续使用
    return flowers

if __name__ == "__main__":
    extract_flowers()