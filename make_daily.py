# make_daily.py
import pandas as pd
import os
import re
from datetime import datetime, timedelta
from sqlalchemy import text
from mysql_conn import engine
from import_order import orders
from inventory_service import deduct_stock, add_stock, get_inventory_report, rollback_daily_sales,get_missing_report_dates

# ============================
# 配置
# ============================
OUTPUT_DIR = r"D:\店铺\日报"
POSTAGE_PER_ORDER = 2.5


# ============================
# 工具函数
# ============================
def ensure_output_dir():
    if not os.path.exists(OUTPUT_DIR):
        os.makedirs(OUTPUT_DIR)


def extract_flower_from_spec(spec):
    """从商品规格中提取花型（逗号或括号前的部分）"""
    if pd.isna(spec):
        return None
    spec = str(spec).strip()
    for sep in [',', '，']:
        if sep in spec:
            flower = spec.split(sep)[0].strip()
            return flower if flower else None
    for sep in ['（', '(']:
        if sep in spec:
            flower = spec.split(sep)[0].strip()
            return flower if flower else None
    return spec if spec else None


def extract_meter_from_spec(spec):
    """提取购买米数（只识别逗号前的长度，避免把宽幅识别进去）"""
    if pd.isna(spec):
        return 0
    spec = str(spec).strip()
    # 只取逗号前内容
    first_part = spec.split(',')[1] if ',' in spec else spec
    first_part = re.split(r'[（(]', first_part)[0]
    first_part = first_part.strip()

    # 中文数字
    chinese_map = {
        "半米": 0.5, "半": 0.5,
        "一米": 1, "二米": 2, "两米": 2, "三米": 3, "四米": 4, "五米": 5,
    }
    for k, v in chinese_map.items():
        if k in first_part:
            return v

    # 数字米
    match = re.search(r'(\d+\.?\d*)\s*(米|m)', first_part, re.I)
    if match:
        return float(match.group(1))
    return 0


def load_cost_map(report_date=None):
    """
    按报表日期加载成本
    规则：未删除 或 删除生效日期 > 报表日期（即删除日期之后才隐藏）
    """
    try:
        if not report_date:
            report_date = datetime.now().strftime("%Y-%m-%d")

        sql = """
              SELECT flower, cost_per_meter
              FROM product_cost
              WHERE is_deleted = 0
                 OR (is_deleted = 1 AND delete_effect_date > %s)
              """
        df = pd.read_sql(sql, engine, params=(report_date,))
        return dict(zip(df['flower'], df['cost_per_meter']))
    except Exception as e:
        print(f"加载成本表失败: {e}")
        return {}
# ============================
# 主函数
# ============================
def generate_daily_report(target_date=None, force=True, orders=orders):
    """
    生成日报（默认强制覆盖）
    force=True：强制重新生成，先回退旧库存，再重新扣减
    """
    ensure_output_dir()

    if target_date is None:
        target_dt = datetime.now()
    else:
        try:
            target_dt = datetime.strptime(target_date, '%Y-%m-%d')
        except ValueError:
            print(f"日期格式错误: {target_date}，请使用 YYYY-MM-DD 格式")
            return

    target_date = target_dt.strftime('%Y-%m-%d')
    filename = f"{target_dt.strftime('%Y%m%d')}日报.xlsx"
    filepath = os.path.join(OUTPUT_DIR, filename)

    # ============================================================
    # 🔧 修复：强制覆盖模式 - 先读取旧数据，再回退库存，最后删除缓存
    # ============================================================
    old_data = None
    with engine.connect() as conn:
        # 1. 检查是否有旧缓存，并读取旧数据用于对比
        existing = conn.execute(
            text("SELECT COUNT(*) FROM daily_report_cache WHERE report_date = :d"),
            {"d": target_date}
        ).scalar()

        if existing > 0:
            old_data = pd.read_sql(
                text("SELECT flower, total_meters, revenue, profit FROM daily_report_cache WHERE report_date = :d"),
                conn,
                params={"d": target_date}
            )
            if not old_data.empty:
                old_data = old_data.set_index('flower').to_dict(orient='index')
            else:
                old_data = {}
            print(f"📊 检测到 {target_date} 已有日报缓存")
        else:
            old_data = {}
            print(f"📝 {target_date} 首次生成日报")

        # 2. 🔧 关键修复：先回退旧库存（无论是否有缓存）
        print(f"🔄 正在回退 {target_date} 的旧库存变动...")
        count, success, msg = rollback_daily_sales(target_date, operator="system")
        if not success:
            raise Exception(f"❌ 回退失败：{msg}")
        if count > 0:
            print(f"✅ 已回退 {count} 条销售出库记录")
        else:
            print(f"ℹ️ 没有需要回退的记录")

        # 3. 删除所有旧缓存（确保插入时不会唯一键冲突）
        conn.execute(
            text("DELETE FROM daily_report_cache WHERE report_date = :d"),
            {"d": target_date}
        )
        conn.execute(
            text("DELETE FROM daily_report_meta WHERE report_date = :d"),
            {"d": target_date}
        )
        conn.execute(
            text("DELETE FROM inventory_shortfall WHERE reference_date = :d"),
            {"d": target_date}
        )
        conn.commit()
        print(f"✅ 已清理 {target_date} 的所有旧缓存数据")

    # ============================================================
    # 读取当天订单
    # ============================================================
    query = text(f"""
        SELECT 
            id, order_no, product, product_spec, 
            product_quantity, merchant_income,
            cost, meter, express_cost, traffic_cost, profit,
            after_sale_status, order_status
        FROM {orders}
        WHERE DATE(order_time) = :target_date
    """)

    try:
        with engine.connect() as conn:
            df = pd.read_sql(query, conn, params={"target_date": target_date})
    except Exception as e:
        print(f"读取数据库失败: {e}")
        return

    if df.empty:
        print(f"⚠️ {target_date} 没有订单数据，无需生成日报")
        return

    original_count = len(df)
    print(f"📋 原始订单数: {original_count}")

    # 加载成本表
    cost_map = load_cost_map(target_date)
    cost_flowers = set(cost_map.keys())
    print(f"📚 成本表中有 {len(cost_map)} 个花型")

    # ============================================================
    # 花型匹配（四层策略）
    # ============================================================
    # 第一步：从规格提取
    df['spec_flower'] = df['product_spec'].apply(extract_flower_from_spec)
    df['花型'] = df['spec_flower'].apply(lambda x: x if x in cost_flowers else None)

    # 第二步：从商品名称匹配
    unmatched_mask = df['花型'].isna()
    if unmatched_mask.any():
        print(f"🔍 有 {unmatched_mask.sum()} 行未从规格匹配到，尝试从商品名称匹配...")
        for flower in cost_flowers:
            still_unmatched = df['花型'].isna()
            if not still_unmatched.any():
                break
            contains = df.loc[still_unmatched, 'product'].str.contains(flower, na=False, case=False)
            if contains.any():
                df.loc[still_unmatched & contains, '花型'] = flower

    # 第三步：商品+规格拼接匹配
    unmatched_mask = df['花型'].isna()
    if unmatched_mask.any():
        print(f"🔍 仍有 {unmatched_mask.sum()} 行未匹配，尝试用「商品-规格」拼接匹配...")
        df['concat_field'] = df['product'].fillna('') + '-' + df['product_spec'].fillna('')
        for flower in cost_flowers:
            still_unmatched = df['花型'].isna()
            if not still_unmatched.any():
                break
            contains = df.loc[still_unmatched, 'concat_field'].str.contains(flower, na=False, case=False)
            if contains.any():
                df.loc[still_unmatched & contains, '花型'] = flower
        df.drop(columns=['concat_field'], inplace=True)

    # 第四步：关键词映射
    unmatched_mask = df['花型'].isna()
    if unmatched_mask.any():
        print(f"🔍 仍有 {unmatched_mask.sum()} 行未匹配，尝试关键词映射匹配...")
        mapping_rules = [
            {'product_keyword': '唐人', 'spec_color_keyword': '紫色', 'target_flower': '唐人紫色'},
            {'product_keyword': '唐人', 'spec_color_keyword': '蓝色', 'target_flower': '唐人蓝色'},
            {'product_keyword': '唐人', 'spec_color_keyword': '粉色', 'target_flower': '唐人粉色'},
            {'product_keyword': '唐人', 'spec_color_keyword': '黄色', 'target_flower': '黄色小唐人'},
            {'product_keyword': '唐人', 'spec_color_keyword': '皮粉', 'target_flower': '唐人粉色'},
            {'product_keyword': '黄底哪吒', 'target_flower': '黄底哪吒'},
            {'product_keyword': '条纹哪吒', 'target_flower': '条纹哪吒'},
            {'product_keyword': '哪吒', 'target_flower': 'Q版哪吒'},
            {'product_keyword': '东北大花', 'spec_keyword': '红底红花', 'target_flower': '东北红底红花'},
            {'product_keyword': '东北大花', 'spec_keyword': '绿底红花', 'target_flower': '东北绿底红花'},
            {'product_keyword': '城市条纹', 'target_flower': '城市条纹'},
            {'product_keyword': '黑白城市条纹', 'target_flower': '城市条纹'},
            {'product_keyword': '油墨风银杏', 'target_flower': '油墨风银杏'},
            {'product_keyword': '银杏叶', 'target_flower': '油墨风银杏'},
            {'product_keyword': '雕印', 'target_flower': '雕印绿色海浪纹'},
            {'product_keyword': '七彩数码印花', 'target_flower': '3D数码印花'},
            {'product_keyword': '3D太阳花', 'target_flower': '3D立体太阳花'},
        ]
        for idx in df[unmatched_mask].index:
            product = str(df.loc[idx, 'product'])
            spec = str(df.loc[idx, 'product_spec'])
            matched = False
            for rule in mapping_rules:
                if 'product_keyword' in rule and rule['product_keyword'] not in product:
                    continue
                if 'spec_color_keyword' in rule and rule['spec_color_keyword'] not in spec:
                    continue
                if 'spec_keyword' in rule and rule['spec_keyword'] not in spec:
                    continue
                target = rule['target_flower']
                if target in cost_flowers:
                    df.loc[idx, '花型'] = target
                    matched = True
                    break
            if not matched and '七彩数码印花' in product:
                if '雕印' in product or '绿色海浪' in product:
                    df.loc[idx, '花型'] = '雕印绿色海浪纹'
                else:
                    df.loc[idx, '花型'] = '3D数码印花'
                matched = True

    df['花型'] = df['花型'].fillna('未匹配')

    # 打印未匹配订单
    final_unmatched = df[df['花型'] == '未匹配']
    if not final_unmatched.empty:
        print("\n" + "=" * 80)
        print("🚫 所有匹配策略均未成功的订单明细（已从日报中过滤，请人工核查）：")
        print("-" * 80)
        print(final_unmatched[['order_no', 'product', 'product_spec']].to_string(index=False))
        print("=" * 80 + "\n")
    else:
        print("✅ 所有订单均成功匹配花型！")

    # 过滤未匹配
    matched_df = df[df['花型'].isin(cost_flowers)].copy()
    unmatched_df = df[~df['花型'].isin(cost_flowers)].copy()

    matched_count = len(matched_df)
    unmatched_count = len(unmatched_df)
    print(f"✅ 匹配成功: {matched_count} 条")
    if unmatched_count > 0:
        print(f"⚠️ 未匹配（将被过滤）: {unmatched_count} 条")

    if matched_df.empty:
        print(f"❌ 没有匹配成功的订单，无法生成日报")
        return

    df = matched_df

    # ============================================================
    # 米数计算
    # ============================================================
    df['单位米数'] = df['product_spec'].apply(extract_meter_from_spec)
    df.loc[df['单位米数'] == 0, '单位米数'] = 1
    df['米数'] = (df['单位米数'] * df['product_quantity']).round(2)
    # 保留0.5米倍数
    df['米数'] = (
            (df['米数'] * 2)
            .round()
            / 2
    ).round(2)
    # 检查非0.5倍数米数
    check = df[
        (df['米数'] * 2 % 1 != 0)
    ]

    if not check.empty:
        print("\n==========异常米数订单==========")
        print(
            check[
                [
                    'product_spec',
                    '单位米数',
                    'product_quantity',
                    '米数'
                ]
            ].to_string(index=False)
        )
    # ============================================================
    # 成本计算
    # ============================================================
    df['单位成本'] = df['花型'].apply(lambda x: cost_map.get(x, 0))
    df['成本'] = (df['单位成本'] * df['米数']).round(2)

    # ============================================================
    # 快递费和盈利
    # ============================================================
    df['快递费'] = 0.0
    first_order = df.drop_duplicates('order_no').index
    df.loc[first_order, '快递费'] = POSTAGE_PER_ORDER
    df['盈利'] = (df['merchant_income'] - df['成本'] - df['快递费']).round(2)

    # ============================================================
    # 退款标记
    # ============================================================
    df['是否退款'] = df['after_sale_status'].astype(str).str.contains('退款成功', na=False)

    # ============================================================
    # 正常订单（汇总表用：排除所有退款和取消）
    # ============================================================
    normal_df = df[(~df['是否退款']) & (df['order_status'] != '已取消')]

    # ============================================================
    # 明细表数据：正常订单 + 已发货/已收货退款订单（用于对账/运费核算）
    # ============================================================
    # 需要保留的退款状态（涉及运费承担）
    keep_refund_statuses = [
        '已发货，退款成功',
        '已收货，退款成功'
    ]

    # 明细表数据：正常订单 + 符合条件的退款订单
    detail_df = df[
        (df['order_status'] != '已取消') &
        (
                (~df['是否退款']) |
                (df['order_status'].isin(keep_refund_statuses))
        )
        ].copy()

    # 退款订单的成本和米数设为 0
    detail_df.loc[detail_df['是否退款'] == True, ['成本', '米数']] = 0

    # ============================================================
    # 明细表
    # ============================================================
    detail_cols = ['花型', '成本', '米数', 'merchant_income', '快递费', '盈利',
                   'after_sale_status', 'order_no', 'product_spec', 'product_quantity', '是否退款']
    detail = detail_df[detail_cols].copy()
    detail = detail.rename(columns={
        'merchant_income': '营业额',
        'after_sale_status': '售后状态',
        'product_spec': '商品规格',
        'product_quantity': '商品数量'
    })
    detail = detail.sort_values('花型')

    # ============================================================
    # 写入日报缓存
    # ============================================================
    if not normal_df.empty:
        with engine.connect() as conn:
            cache_data = normal_df.groupby('花型').agg(
                order_count=('order_no', 'nunique'),
                total_meters=('米数', 'sum'),
                revenue=('merchant_income', 'sum'),
                cost=('成本', 'sum'),
                express_fee=('快递费', 'sum'),
                profit=('盈利', 'sum')
            ).reset_index()

            for _, row in cache_data.iterrows():
                conn.execute(
                    text("""
                        INSERT INTO daily_report_cache 
                        (report_date, flower, order_count, total_meters, revenue, cost, express_fee, profit)
                        VALUES (:d, :f, :oc, :tm, :rev, :cst, :exp, :prf)
                    """),
                    {
                        "d": target_date,
                        "f": row['花型'],
                        "oc": int(row['order_count']),
                        "tm": float(row['total_meters']),
                        "rev": float(row['revenue']),
                        "cst": float(row['cost']),
                        "exp": float(row['express_fee']),
                        "prf": float(row['profit'])
                    }
                )
            conn.commit()
            print(f"✅ 日报缓存已写入：{len(cache_data)} 个花型")

    # ============================================================
    # 变化报告（对比新旧数据）
    # ============================================================
    if old_data and not normal_df.empty:
        print("\n" + "=" * 60)
        print(f"📊 {target_date} 日报变化报告：")
        print("-" * 60)

        new_data = normal_df.groupby('花型').agg(
            total_meters=('米数', 'sum'),
            revenue=('merchant_income', 'sum'),
            profit=('盈利', 'sum')
        ).to_dict(orient='index')

        all_flowers = set(old_data.keys()) | set(new_data.keys())
        changes = []
        for flower in sorted(all_flowers):
            old = old_data.get(flower, {})
            new = new_data.get(flower, {})
            old_m = old.get('total_meters', 0)
            new_m = new.get('total_meters', 0)
            old_r = old.get('revenue', 0)
            new_r = new.get('revenue', 0)
            old_p = old.get('profit', 0)
            new_p = new.get('profit', 0)

            if abs(old_m - new_m) > 0.01 or abs(old_r - new_r) > 0.01:
                changes.append({
                    '花型': flower,
                    '旧米数': old_m,
                    '新米数': new_m,
                    '米数变化': new_m - old_m,
                    '旧营业额': old_r,
                    '新营业额': new_r,
                    '营业额变化': new_r - old_r,
                    '旧利润': old_p,
                    '新利润': new_p,
                    '利润变化': new_p - old_p
                })

        if changes:
            change_df = pd.DataFrame(changes)
            print(change_df.to_string(index=False))
            print(f"\n📌 共有 {len(changes)} 个花型发生变化")
        else:
            print("✅ 数据无变化（但已强制重新生成）")

        print("=" * 60 + "\n")
    elif old_data and normal_df.empty:
        print(f"⚠️ {target_date} 当前无正常订单，旧数据已被清除")

    # ============================================================
    # 自动扣减库存
    # ============================================================
    if not normal_df.empty:
        sales_summary = normal_df.groupby('花型')['米数'].sum().reset_index()
        print("\n📦 正在扣减库存...")
        for _, row in sales_summary.iterrows():
            flower = row['花型']
            meters = float(row['米数'])
            if meters > 0:
                try:
                    deduct_stock(
                        flower=flower,
                        qty=meters,
                        reference=f"日报自动扣减 {target_date}",
                        operator="system",
                        report_date=target_date
                    )
                except ValueError as e:
                    print(f"⚠️ {e}，跳过该花型")
        print("✅ 库存扣减完成")

    # ============================================================
    # 查询缺口
    # ============================================================
    with engine.connect() as conn:
        gaps = conn.execute(
            text("""
                SELECT flower, shortfall_meters 
                FROM inventory_shortfall 
                WHERE reference_date = :d AND status = '待补录'
            """),
            {"d": target_date}
        ).fetchall()

    if gaps:
        print("\n" + "=" * 50)
        print("📋 以下花型库存不足，已产生缺口（请尽快补录）：")
        for row in gaps:
            print(f"   {row[0]}: 缺口 {row[1]} 米")
        print("=" * 50)
    else:
        print("✅ 当天所有花型库存充足，无缺口")

    # ============================================================
    # 汇总表
    # ============================================================
    if normal_df.empty:
        summary = pd.DataFrame(columns=['花型', '订单数', '成本', '米数', '营业额', '快递费', '盈利'])
    else:
        summary = normal_df.groupby('花型').agg(
            订单数=('order_no', 'nunique'),
            成本=('成本', 'sum'),
            米数=('米数', 'sum'),
            营业额=('merchant_income', 'sum'),
            快递费=('快递费', 'sum'),
            盈利=('盈利', 'sum')
        ).reset_index()
        for col in ['成本', '米数', '营业额', '快递费', '盈利']:
            summary[col] = summary[col].round(2)
        summary = summary.sort_values('营业额', ascending=False)

    total_row = pd.DataFrame({
        '花型': ['【总计】'],
        '订单数': [len(normal_df)],
        '成本': [normal_df['成本'].sum().round(2)] if not normal_df.empty else [0],
        '米数': [normal_df['米数'].sum().round(2)] if not normal_df.empty else [0],
        '营业额': [normal_df['merchant_income'].sum().round(2)] if not normal_df.empty else [0],
        '快递费': [normal_df['快递费'].sum().round(2)] if not normal_df.empty else [0],
        '盈利': [normal_df['盈利'].sum().round(2)] if not normal_df.empty else [0]
    })
    summary = pd.concat([summary, total_row], ignore_index=True)




    # 打印总计对比
    detail_total_cost = detail['成本'].sum()
    detail_total_meter = detail['米数'].sum()
    summary_total_cost = normal_df['成本'].sum() if not normal_df.empty else 0
    summary_total_meter = normal_df['米数'].sum() if not normal_df.empty else 0
    print(f"\n总计对比：")
    print(f"  明细总成本: {detail_total_cost:.2f}  汇总总成本: {summary_total_cost:.2f}  差异: {summary_total_cost - detail_total_cost:.2f}")
    print(f"  明细总米数: {detail_total_meter:.2f}  汇总总米数: {summary_total_meter:.2f}  差异: {summary_total_meter - detail_total_meter:.2f}")
    print("=" * 80 + "\n")





    # ============================================================
    # 保存 Excel
    # ============================================================
    with pd.ExcelWriter(filepath, engine='openpyxl') as writer:
        summary.to_excel(writer, sheet_name='花型汇总', index=False)
        detail.to_excel(writer, sheet_name='订单明细', index=False)

        for sheet_name in writer.sheets:
            worksheet = writer.sheets[sheet_name]
            for column in worksheet.columns:
                max_length = 0
                column_letter = column[0].column_letter
                for cell in column:
                    try:
                        if len(str(cell.value)) > max_length:
                            max_length = len(str(cell.value))
                    except:
                        pass
                adjusted_width = min(max_length + 2, 30)
                worksheet.column_dimensions[column_letter].width = adjusted_width

    print(f"✅ 日报已生成: {filepath}")
    if not normal_df.empty:
        print(f"📊 正常订单 {len(normal_df)} 单，总营业额 {normal_df['merchant_income'].sum():.2f}，总盈利 {normal_df['盈利'].sum():.2f}")
    if unmatched_count > 0:
        print(f"⚠️ 已过滤 {unmatched_count} 条未匹配花型的订单")
    # ============================================================
    # 🆕 生成库存快照
    # ============================================================
    print("\n📸 正在生成库存快照...")
    from inventory_service import fill_missing_snapshots, get_missing_report_dates

    # 补全缺失的快照（从上次快照到当天）
    filled, status, msg = fill_missing_snapshots(up_to_date=None, operator="system")

    if status == "no_report":
        print(f"⚠️ {msg}")
    elif status == "already_latest":
        print(f"ℹ️ {msg}")
    elif status == "error":
        print(f"❌ {msg}")
    else:
        print(f"✅ {msg}")

    # 检查是否有缺失的日报（自动提醒）
    missing_reports = get_missing_report_dates()
    if missing_reports:
        print(f"⚠️ 以下日期有订单但未生成日报：{missing_reports}")
    return filepath


# ============================
# 批量补全函数
# ============================
def generate_all_missing_reports(orders=orders):
    ensure_output_dir()
    query = text(f"""
        SELECT DISTINCT DATE(order_time) AS order_date
        FROM {orders}
        WHERE order_time IS NOT NULL
        ORDER BY order_date
    """)
    with engine.connect() as conn:
        df_dates = pd.read_sql(query, conn)

    if df_dates.empty:
        print("没有订单数据")
        return

    # 🔧 过滤掉 None 值
    df_dates = df_dates.dropna(subset=['order_date'])

    print(f"找到 {len(df_dates)} 个有订单的日期")
    generated = 0
    for _, row in df_dates.iterrows():
        date_ymd = row['order_date'].strftime('%Y-%m-%d')
        print(f"  📊 生成 {date_ymd} 日报...")
        generate_daily_report(date_ymd, force=True)
        generated += 1
    print(f"\n✅ 完成: 生成 {generated} 个")

# ============================
# 入口
# ============================
if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1:
        if sys.argv[1] == "--all":
            generate_all_missing_reports()
        elif sys.argv[1] == "--range" and len(sys.argv) == 4:
            start = sys.argv[2]
            end = sys.argv[3]
            current = datetime.strptime(start, '%Y-%m-%d')
            end_dt = datetime.strptime(end, '%Y-%m-%d')
            while current <= end_dt:
                generate_daily_report(current.strftime('%Y-%m-%d'), force=True)
                current += timedelta(days=1)
        else:
            generate_daily_report(sys.argv[1], force=True)
    else:
            # 默认生成指定日期的日报（可自行修改日期）
            generate_daily_report("2026-07-11")