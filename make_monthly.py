# make_monthly.py

import pandas as pd
import os
import re

from datetime import datetime

from sqlalchemy import text

from mysql_conn import engine

from import_order import orders, PLATFORM_NAMES
from make_daily import build_platform_summary, build_platform_totals, write_summary_sheet, _auto_width_sheets, CANCELLED_STATUSES, FLOWER_ALIAS_MAP
from report_date_logic import ORDER_DATE_SQL
# ==========================================================
# 配置
# ==========================================================

OUTPUT_DIR = r"D:\店铺\月报"

POSTAGE_PER_ORDER = 2.5



# ==========================================================
# 工具函数
# ==========================================================


def ensure_output_dir():

    """
    创建输出目录
    """

    if not os.path.exists(OUTPUT_DIR):

        os.makedirs(OUTPUT_DIR)



# ----------------------------------------------------------

def extract_flower_from_spec(spec):

    """
    从商品规格中提取花型

    示例:

    玄纹密码,一米
        ->
    玄纹密码


    秋韵金叶（180克）
        ->
    秋韵金叶

    """

    if pd.isna(spec):

        return None


    spec = str(spec).strip()



    # 逗号/分号分隔，取第一段（分号用于抖音规格「花型;二米」，拼多多/淘宝无分号）

    for sep in [',','，',';','；']:

        if sep in spec:

            spec = spec.split(sep)[0].strip()

            break



    # 中英文括号（门幅/克重等说明）

    for sep in ['（','(']:

        if sep in spec:

            spec = spec.split(sep)[0].strip()

            break



    return spec if spec else None





# ----------------------------------------------------------

def extract_meter_from_spec(spec):

    """
    提取规格中的米数

    例如：

    一米 -> 1

    2米 -> 2

    1.5m ->1.5

    """

    if pd.isna(spec):

        return 0



    spec=str(spec)



    meter_map={

        '一米':1,

        '1米':1,

        '1m':1,


        '二米':2,

        '2米':2,

        '2m':2,


        '三米':3,

        '3米':3,

        '3m':3,


        '四米':4,

        '4米':4,

        '4m':4,


        '五米':5,

        '5米':5,

        '5m':5,


        '两米':2,


        '0.5米':0.5,

        '0.5m':0.5,


        '1.5米':1.5,

        '1.5m':1.5,


        '2.5米':2.5,

        '2.5m':2.5

    }



    for key,value in meter_map.items():

        if key in spec:

            return value



    # 正则匹配

    match=re.search(

        r'(\d+\.?\d*)\s*[米m]',

        spec

    )


    if match:

        return float(match.group(1))


    return 0






# ----------------------------------------------------------

def load_cost_map(biz_date):
    """
    读取商品成本，biz_date=报表日期
    规则：未删除 OR 删除生效日期 > 报表日期，当月仍参与统计
    """
    try:
        sql="""
        SELECT
            flower,
            cost_per_meter
        FROM product_cost
        WHERE is_deleted = 0 OR delete_effect_date > :biz_date
        """
        df=pd.read_sql(text(sql), engine, params={"biz_date": biz_date})
        return dict(zip(df['flower'], df['cost_per_meter']))
    except Exception as e:
        print(f"读取成本表失败:{e}")
        return {}






# ==========================================================
# 月报主函数
# ==========================================================


def generate_monthly_report(start_date,end_date,force=False,orders=orders):


    """

    生成指定时间范围月报


    参数:

    start_date:

        开始日期

        例如:

        2026-07-01



    end_date:

        结束日期

        例如:

        2026-07-31



    force:

        是否覆盖已有文件

    """



    ensure_output_dir()





    # ======================================================
    # 日期检查
    # ======================================================


    try:


        datetime.strptime(

            start_date,

            "%Y-%m-%d"

        )


        datetime.strptime(

            end_date,

            "%Y-%m-%d"

        )


    except ValueError:


        print(

            "❌ 日期格式错误，应为 YYYY-MM-DD"

        )


        return





    # ======================================================
    # 文件路径
    # ======================================================


    filename=(

        f"{start_date.replace('-','')}"

        "-"

        f"{end_date.replace('-','')}"

        "月报.xlsx"

    )



    filepath=os.path.join(

        OUTPUT_DIR,

        filename

    )





    if os.path.exists(filepath) and not force:


        print(

            f"⏭️ {filename} 已存在，跳过生成"

        )


        return





    print(

        f"📊 正在生成 {start_date} 到 {end_date} 月报..."

    )





    # ======================================================
    # 读取时间范围订单
    # ======================================================


    # 按下单日期统计：拼多多订单号前6位 / 淘宝支付单号前8位 / 抖音订单完成时间
    sql=text(f"""
        SELECT
            id, order_no, product, product_spec,
            product_quantity, merchant_income,
            cost, meter, express_cost, traffic_cost, profit,
            after_sale_status, order_status, platform,
            delivery_time
        FROM (
            SELECT
                id, order_no, product, product_spec,
                product_quantity, merchant_income,
                cost, meter, express_cost, traffic_cost, profit,
                after_sale_status, order_status, platform,
                delivery_time,
                {ORDER_DATE_SQL} AS order_date
            FROM {orders}
        ) t
        WHERE t.order_date >= :start_date
          AND t.order_date < DATE_ADD(:end_date, INTERVAL 1 DAY)
    """)




    try:


        with engine.connect() as conn:


            df=pd.read_sql(

                sql,

                conn,

                params={


                    "start_date":start_date,


                    "end_date":end_date

                }

            )



    except Exception as e:


        print(

            f"❌ 读取订单失败:{e}"

        )


        return





    if df.empty:


        print(

            f"⚠️ {start_date} 到 {end_date} 没有订单数据"

        )


        return





    print(

        f"📋 时间范围订单数量:{len(df)} 条"

    )


    # 平台显示名（0=拼多多, 1=淘宝, 2=抖音；旧数据默认拼多多）
    df['平台'] = df['platform'].map(PLATFORM_NAMES).fillna('拼多多')



    # ======================================================
    # 后面进入花型匹配
    # ======================================================

    cost_map = load_cost_map(end_date)

    cost_flowers=set(cost_map.keys())


    print(

        f"📚 成本表花型数量:{len(cost_flowers)}"

    )
    # ==========================================================
    # 花型匹配逻辑
    #
    # ① 商品规格精确匹配
    # ② 商品名称包含匹配
    # ③ 商品+规格组合匹配
    # ④ 特殊关键词映射
    # ==========================================================


    # ----------------------------------------------------------
    # 第一层：
    # 从商品规格提取花型
    # ----------------------------------------------------------

    df['spec_flower'] = df['product_spec'].apply(
        extract_flower_from_spec
    )

    # 花型标准化映射：先做别名归一，再做成本表匹配（与日报口径一致）
    df['spec_flower'] = df['spec_flower'].replace(FLOWER_ALIAS_MAP)

    df['花型'] = df['spec_flower'].apply(
        lambda x:
        x if x in cost_flowers else None
    )

    # ----------------------------------------------------------
    # 第二层：
    # 商品名称包含匹配
    # ----------------------------------------------------------

    unmatched = df['花型'].isna()


    if unmatched.any():

        print(
            f"🔍 {unmatched.sum()} 条规格未匹配，尝试商品名称匹配..."
        )


        for flower in cost_flowers:


            still = df['花型'].isna()


            if not still.any():

                break



            result = df.loc[
                still,
                'product'
            ].str.contains(

                flower,

                na=False

            )


            if result.any():

                df.loc[
                    still & result,
                    '花型'
                ] = flower






    # ----------------------------------------------------------
    # 第三层：
    # 商品+规格联合匹配
    # ----------------------------------------------------------


    unmatched=df['花型'].isna()



    if unmatched.any():

        print(
            f"🔍 {unmatched.sum()} 条继续联合匹配..."
        )


        df['match_text']=(

            df['product'].fillna('')

            +

            df['product_spec'].fillna('')

        )



        for flower in cost_flowers:


            still=df['花型'].isna()


            if not still.any():

                break



            result=df.loc[
                still,
                'match_text'
            ].str.contains(

                flower,

                na=False

            )



            if result.any():

                df.loc[
                    still & result,
                    '花型'
                ]=flower



        df.drop(

            columns=['match_text'],

            inplace=True

        )







    # ----------------------------------------------------------
    # 第四层：
    # 特殊商品映射
    # ----------------------------------------------------------


    mapping_rules=[


        {
            "keyword":"唐人",
            "color":"紫色",
            "target":"唐人紫色"
        },


        {
            "keyword":"唐人",
            "color":"蓝色",
            "target":"唐人蓝色"
        },


        {
            "keyword":"唐人",
            "color":"粉色",
            "target":"唐人粉色"
        },


        {
            "keyword":"唐人",
            "color":"黄色",
            "target":"黄色小唐人"
        },


        {
            "keyword":"黄底哪吒",
            "target":"黄底哪吒"
        },


        {
            "keyword":"条纹哪吒",
            "target":"条纹哪吒"
        },


        {
            "keyword":"哪吒",
            "target":"Q版哪吒"
        },


        {
            "keyword":"东北大花",
            "color":"红底红花",
            "target":"东北红底红花"
        },


        {
            "keyword":"东北大花",
            "color":"绿底红花",
            "target":"东北绿底红花"
        },


        {
            "keyword":"城市条纹",
            "target":"城市条纹"
        },


        {
            "keyword":"黑白城市条纹",
            "target":"城市条纹"
        },


        {
            "keyword":"油墨风银杏",
            "target":"油墨风银杏"
        },


        {
            "keyword":"银杏叶",
            "target":"油墨风银杏"
        },


        {
            "keyword":"雕印",
            "target":"雕印绿色海浪纹"
        },


        {
            "keyword":"七彩数码印花",
            "target":"3D数码印花"
        }


    ]






    for idx,row in df[df['花型'].isna()].iterrows():


        product=str(row['product'])

        spec=str(row['product_spec'])



        for rule in mapping_rules:


            if rule['keyword'] not in product:

                continue



            if 'color' in rule:

                if rule['color'] not in spec:

                    continue



            target=rule['target']



            if target in cost_flowers:


                df.loc[
                    idx,
                    '花型'
                ]=target


                break






    # ----------------------------------------------------------
    # 未匹配处理
    # ----------------------------------------------------------


    df['花型']=df['花型'].fillna(

        '未匹配'

    )



    final_unmatched=df[

        df['花型']=='未匹配'

    ]



    if not final_unmatched.empty:


        print("\n"+"="*80)

        print(
            "🚫 以下订单未匹配花型:"
        )

        print(

            final_unmatched[

                [
                    'order_no',
                    'product',
                    'product_spec'
                ]

            ].to_string(index=False)

        )

        print("="*80+"\n")


    else:


        print(
            "✅ 所有订单花型匹配成功"
        )






    # 未匹配订单：标为「待建成本」，成本/盈利=0，营业额真实保留（进报表单独汇总）
    unmatched_df = df[df['花型'] == '未匹配'].copy()
    if not unmatched_df.empty:
        unmatched_df['花型'] = '待建成本'
        unmatched_df['平台'] = unmatched_df['platform'].map(PLATFORM_NAMES).fillna('拼多多')
        unmatched_df['单位米数'] = unmatched_df['product_spec'].apply(extract_meter_from_spec)
        unmatched_df.loc[unmatched_df['单位米数'] == 0, '单位米数'] = 1
        unmatched_df['米数'] = (unmatched_df['单位米数'] * unmatched_df['product_quantity']).round(2)
        unmatched_df['成本'] = 0.0
        unmatched_df['快递费'] = 0.0
        unmatched_df['盈利'] = 0.0
        unmatched_df['是否退款'] = (
            unmatched_df['after_sale_status'].astype(str).str.contains('退款成功', na=False)
            | unmatched_df['order_status'].astype(str).str.contains('退款成功', na=False)
        )

    # ==========================================================
    # 过滤未匹配订单
    # ==========================================================


    df=df[

        df['花型'].isin(cost_flowers)

    ].copy()



    if df.empty:


        print(

            "❌ 没有有效订单"

        )


        return





    print(

        f"✅ 有效订单:{len(df)} 条"

    )





    # ==========================================================
    # 米数计算
    # ==========================================================


    df['单位米数']=df['product_spec'].apply(

        extract_meter_from_spec

    )



    df.loc[

        df['单位米数']==0,

        '单位米数'

    ]=1




    # 月报：

    # 总米数 = 单件米数 × 商品数量


    df['米数']=(

        df['单位米数']

        *

        df['product_quantity']

    ).round(2)





    # ==========================================================
    # 成本计算
    # ==========================================================


    df['单位成本']=df['花型'].apply(

        lambda x:

        cost_map.get(x,0)

    )


    df['成本']=(

        df['单位成本']

        *

        df['米数']

    ).round(2)





    # ==========================================================
    # 盈利计算
    # ==========================================================


    df['快递费']=POSTAGE_PER_ORDER



    df['盈利']=(

        df['merchant_income']

        -

        df['成本']

        -

        df['快递费']

    ).round(2)






    # ==========================================================
    # 售后退款
    # ==========================================================


    # 退款标记（与日报口径一致）：售后状态 或 订单状态 含「退款成功」都算退款
    df['是否退款']=(
        df['after_sale_status'].astype(str).str.contains('退款成功', na=False)
        | df['order_status'].astype(str).str.contains('退款成功', na=False)
    )




    print(

        f"📦 退款订单:{df['是否退款'].sum()} 条"

    )
    # ==========================================================
    # 月报订单明细
    # ==========================================================


    detail_cols=[

        '花型',

        '平台',

        '成本',

        '米数',

        'merchant_income',

        '快递费',

        '盈利',

        'after_sale_status',

        'order_no',

        'product_spec',

        'product_quantity',

        '是否退款'

    ]



    detail=df[detail_cols].copy()



    detail.rename(

        columns={

            'merchant_income':'营业额',

            'after_sale_status':'售后状态',

            'product_spec':'商品规格',

            'product_quantity':'商品数量'

        },

        inplace=True

    )



    detail=detail.sort_values(

        by='花型'

    )






    # ==========================================================
    # ==========================================================
    # 月报花型汇总（左右分列：花型 | 拼多多6列 | 空2列 | 淘宝6列 | 空2列 | 汇总6列）
    # ==========================================================


    normal_df=df[

        (~df['是否退款'])

        &

        (~df['order_status'].isin(CANCELLED_STATUSES))

    ]

    summary_wide = build_platform_summary(normal_df, total_label="【月度总计】")
    platform_summary = build_platform_totals(normal_df)
    # 未匹配订单：追加「待建成本」汇总/明细，避免营业额被静默过滤
    from make_daily import append_pending_cost_rows, append_pending_cost_summary
    detail = append_pending_cost_rows(detail, unmatched_df)
    summary_wide, platform_summary = append_pending_cost_summary(summary_wide, platform_summary, unmatched_df)






    # ==========================================================
    # 月报统计信息
    # ==========================================================


    refund_count=int(

        df['是否退款'].sum()

    )



    refund_amount=(

        df.loc[

            df['是否退款'],

            'merchant_income'

        ].sum()

        .round(2)

    )



    print("\n"+"="*50)


    print(

        f"📊 月报周期:{start_date} ~ {end_date}"

    )


    print(

        f"📦 有效订单:{len(df)} 单"

    )


    print(

        f"💰 总营业额:{normal_df['merchant_income'].sum():.2f}"

    )


    print(

        f"📉 总成本:{normal_df['成本'].sum():.2f}"

    )


    print(

        f"🔥 总盈利:{normal_df['盈利'].sum():.2f}"

    )


    print(

        f"↩️ 退款订单:{refund_count} 单"

    )


    print(

        f"↩️退款金额:{refund_amount:.2f}"

    )


    print("="*50)








    # ==========================================================
    # 保存Excel月报
    # ==========================================================


    with pd.ExcelWriter(

        filepath,

        engine='openpyxl'

    ) as writer:

        write_summary_sheet(writer, summary_wide, sheet_name="花型汇总")
        platform_summary.to_excel(writer, sheet_name="平台汇总", index=False)
        detail.to_excel(writer, sheet_name="订单明细", index=False)
        _auto_width_sheets(writer)

    print(
        f"✅ 月报生成成功:{filepath}"
    )

    return filepath






# ==========================================================
# 主程序入口
# ==========================================================


if __name__=="__main__":



    generate_monthly_report(


        start_date="2026-05-01",
        end_date="2026-07-30",


        force=True


    )
