# app.py
import os
import streamlit as st
import pandas as pd
from datetime import datetime, date, timedelta
from inventory_service import (
    get_inventory_report,
    add_stock,
    deduct_stock,
    write_off_stock,
    get_stock_log,
    rollback_inventory_to_date,
    get_current_stock
)
from add_del_flower import (
    add_flower,
    delete_flower,
    get_all_flowers,
    restore_flower
)
import time
from sqlalchemy import text
from mysql_conn import engine
# ============================
# 辅助函数：获取花型列表（供下拉选择用）
# ============================
@st.cache_data(ttl=300)
def get_flower_list():
    """获取当天可用的花型（只显示未删除的）"""
    from add_del_flower import get_available_flowers
    df = get_available_flowers()
    return df['flower'].tolist()
# ============================
# 页面配置
# ============================
st.set_page_config(page_title="布料店库存管理系统", layout="wide")
st.title("🧵 布料店库存管理系统")

# ============================
# 侧边栏菜单
# ============================
menu = st.sidebar.radio(
    "导航菜单",

    ["🏠 首页", "📤 导入订单","📦 库存管理", "📥 入库登记", "📤 出库登记","🌸 花型管理",

     "📋 库存流水", "🚨 预警中心", "📊 报告生成","📊 退款明细", "⚙️ 系统设置"]
)
st.sidebar.markdown("---")
st.sidebar.caption(f"当前时间：{datetime.now().strftime('%Y-%m-%d %H:%M')}")



if menu == "🏠 首页":
    from system_service import get_system_start_date
    from inventory_service import get_system_status, get_missing_report_dates

    start_date = get_system_start_date()

    st.header("🏠 系统状态看板")
    st.caption(f"📅 系统数据起始日期：**{start_date.strftime('%Y-%m-%d')}**（所有数据只显示此日期之后）")

    sys_status = get_system_status()  # 重命名为 sys_status

    col1, col2, col3, col4 = st.columns(4)
    with col1:
        latest = sys_status.get('latest_snapshot')
        st.metric("📅 最新库存日期", latest.strftime('%Y-%m-%d') if latest else "无")
    with col2:
        latest_report = sys_status.get('latest_report')
        st.metric("📊 最新日报日期", latest_report.strftime('%Y-%m-%d') if latest_report else "无")
    with col3:
        pending = sys_status.get('pending_update_days', 0)
        st.metric("⏳ 待更新天数", f"{pending} 天")
    with col4:
        missing_reports = get_missing_report_dates()
        st.metric("⚠️ 缺失日报", f"{len(missing_reports)} 天", delta="请尽快生成" if len(missing_reports) > 0 else None)

    st.divider()

    # 缺失日报
    if missing_reports:
        st.subheader("📋 缺失日报日期")
        missing_dates = [d.strftime('%Y-%m-%d') for d in missing_reports if d is not None]
        if len(missing_dates) > 30:
            display_dates = missing_dates[:30]
            st.write(", ".join(display_dates) + f", ... 共 {len(missing_dates)} 天")
        else:
            st.write(", ".join(missing_dates))

        if st.button("一键生成所有缺失日报"):
            with st.spinner("正在生成..."):
                from make_daily import generate_all_missing_reports

                generate_all_missing_reports()
                st.success("✅ 所有缺失日报已生成")
                st.rerun()
    else:
        st.success("✅ 所有起始日期之后的订单均已生成日报")

    # 缺失快照
    missing_snapshots = sys_status.get('missing_snapshots', [])
    if missing_snapshots:
        st.subheader("📸 缺失库存快照")
        valid_snap = [d for d in missing_snapshots if d is not None and d >= start_date]
        if valid_snap:
            snap_dates = [d.strftime('%Y-%m-%d') for d in valid_snap]
            if len(snap_dates) > 30:
                display_dates = snap_dates[:30]
                st.write(", ".join(display_dates) + f", ... 共 {len(snap_dates)} 天")
            else:
                st.write(", ".join(snap_dates))

            if st.button("补全缺失快照"):
                with st.spinner("正在补全..."):
                    from inventory_service import fill_missing_snapshots

                    filled, fill_status, msg = fill_missing_snapshots(operator="web")
                    if fill_status == "no_report":
                        st.warning(msg)
                    elif fill_status == "already_latest":
                        st.info(msg)
                    elif fill_status == "error":
                        st.error(msg)
                    else:
                        st.success(msg)
                        st.rerun()
        else:
            st.success("✅ 库存快照完整")
    else:
        st.success("✅ 库存快照完整")


elif menu == "📤 导入订单":
    st.header("📤 导入订单 Excel")
    st.info("💡 上传拼多多/淘宝导出的订单明细 Excel 文件，系统会自动识别平台并清洗导入")

    # 文件上传
    uploaded_file = st.file_uploader(
        "选择 Excel 文件",
        type=["xlsx", "xls","csv"],
        help="支持 .xlsx/.xls/.csv，拼多多（订单号/商品/商家实收金额）或淘宝（订单编号/商品标题/买家应付货款）格式，自动识别"
    )

    if uploaded_file is not None:
        # 显示文件信息
        st.write(f"📄 文件名：{uploaded_file.name}")
        st.write(f"📏 文件大小：{uploaded_file.size / 1024:.2f} KB")

        # 换了一个新文件时，清空上一次导入结果，避免残留显示
        new_file_id = uploaded_file.file_id
        if st.session_state.get("uploaded_file_id") != new_file_id:
            st.session_state.pop("import_result", None)
            st.session_state.pop("import_just_done", None)
            st.session_state.pop("import_regen_msg", None)
            st.session_state["uploaded_file_id"] = new_file_id

        if st.button("🚀 开始导入", type="primary", use_container_width=True):
            try:
                import io

                file_content = uploaded_file.read()
                if uploaded_file.name.endswith('.csv'):
                    df = pd.read_csv(io.BytesIO(file_content))
                else:
                    df = pd.read_excel(io.BytesIO(file_content), sheet_name=0, engine='openpyxl')

                with st.spinner("正在导入数据..."):
                    # 调用导入函数（需要从 import_order 导入）
                    from import_order import import_excel_from_dataframe

                    result = import_excel_from_dataframe(df, uploaded_file.name)
                    # 结果存入 session_state，跨重跑保留。
                    # 若不持久化，点击下方「一键重新生成」会触发整体重跑，
                    # 「开始导入」按钮返回 False，导致结果区不再渲染、回调失效。
                    st.session_state["import_result"] = result
                    st.session_state["import_just_done"] = True
            except Exception as e:
                st.error(f"❌ 读取文件失败：{e}")
                st.session_state.pop("import_result", None)
                import traceback

                st.code(traceback.format_exc())

        # 导入结果展示（放在按钮之外，避免按钮嵌套导致回调失效）
        import_result = st.session_state.get("import_result")
        if import_result and import_result["success"]:
            if st.session_state.pop("import_just_done", False):
                st.balloons()
            regen_msg = st.session_state.pop("import_regen_msg", None)
            if regen_msg:
                st.success(regen_msg)
            st.success(f"✅ {import_result['message']}")

            # 📅 检测受影响日期，一键重新生成日报（放在靠上位置，无需下滑即可点击）
            affected_dates = import_result.get("affected_dates") or []
            if affected_dates:
                st.divider()
                st.info(
                    f"📅 检测到 {len(affected_dates)} 个日期的订单数据发生变化："
                    f"{', '.join(affected_dates)}"
                )
                if st.button("🚀 一键重新生成这些日期的日报", type="primary"):
                    from make_daily import generate_daily_report

                    progress_bar = st.progress(0)
                    status_text = st.empty()
                    total = len(affected_dates)
                    failed_dates = []
                    for i, date_str in enumerate(affected_dates):
                        status_text.text(f"正在生成 {date_str} 日报...")
                        try:
                            generate_daily_report(date_str, force=True)
                        except Exception as e:
                            failed_dates.append(date_str)
                            status_text.text(f"⚠️ {date_str} 日报生成失败：{e}")
                        progress_bar.progress((i + 1) / total)
                    # 清空受影响日期，防止重复点击重复生成
                    import_result["affected_dates"] = []
                    if failed_dates:
                        st.session_state["import_regen_msg"] = (
                            f"⚠️ {len(failed_dates)} 个日期日报生成失败：{', '.join(failed_dates)}"
                        )
                    else:
                        st.session_state["import_regen_msg"] = f"✅ {total} 个变化日期的日报已重新生成！"
                    st.balloons()
                    st.rerun()

            # 🔧 显示订单变化（重点标注利润影响）
            changes = import_result.get("changes")
            if changes and changes.get("changes") is not None and not changes["changes"].empty:
                st.divider()
                ch_summary = changes["summary"]
                st.subheader("🔍 订单变化检测")

                col_c1, col_c2, col_c3, col_c4 = st.columns(4)
                with col_c1:
                    st.metric("🆕 新订单", ch_summary.get("new_orders", 0))
                with col_c2:
                    st.metric("📝 变更订单", ch_summary.get("changed_orders", 0))
                with col_c3:
                    st.metric("✅ 无变化", ch_summary.get("unchanged_orders", 0))
                with col_c4:
                    important = ch_summary.get("important_changes", 0)
                    st.metric("🔴 重要变化", important,
                              delta="需关注" if important > 0 else None)

                ch_df = changes["changes"]
                # 样式着色
                def color_changes(row):
                    if row['重要程度'] == '🔴 重要':
                        return ['background-color: #ffe0e0'] * len(row)
                    elif row['重要程度'] == '🟡 关注':
                        return ['background-color: #fff3cd'] * len(row)
                    elif row['变化类型'] == '🆕 新订单':
                        return ['background-color: #e0f0ff'] * len(row)
                    return [''] * len(row)

                st.dataframe(
                    ch_df.style.apply(color_changes, axis=1),
                    use_container_width=True,
                    height=300
                )
                st.caption(
                    "🔴 红色 = 影响利润的重要变化（状态/金额） | "
                    "🟡 黄色 = 需关注（数量/规格） | "
                    "🔵 蓝色 = 新订单"
                )
            elif changes and changes.get("changes") is not None and changes["changes"].empty:
                st.info("📋 所有订单数据与数据库一致，无变化")

            # 显示导入统计
            st.subheader("📊 导入统计")
            st.json(import_result["stats"])

            # 同步退款明细
            st.info("🔄 正在同步退款明细...")
            try:
                from populate_refund_details import sync_refund_details

                sync_refund_details()
                st.success("✅ 退款明细同步完成")
            except Exception as e:
                st.warning(f"⚠️ 退款明细同步失败（不影响主导入）：{e}")
        elif import_result and not import_result["success"]:
            st.error(f"❌ 导入失败：{import_result['message']}")

# ============================
# 2. 入库登记
# ============================
elif menu == "📥 入库登记":
    st.header("📥 入库登记")

    with st.form("add_stock_form"):
        col1, col2 = st.columns(2)
        with col1:
            flower_list = get_flower_list()
            flower = st.selectbox(
                "花型名称 *",
                options=flower_list,
                placeholder="输入花型名称搜索...",
                help="支持模糊搜索，输入关键词自动过滤",
                index=None,
            )
            qty = st.number_input("入库米数 *", min_value=0.5, step=0.5, format="%.1f")
        with col2:
            stock_date = st.date_input("入库日期", value=date.today(), help="入库生效日期，该日期及之后的库存都会增加")
            ref = st.text_input("备注", placeholder="例：2026-07-25 第一批进货")
            operator = st.text_input("操作人", value="admin")

        submitted = st.form_submit_button("✅ 确认入库")

        if submitted:
            if not flower or qty <= 0:
                st.error("请填写完整信息（花型和米数必填）")
            else:
                try:
                    add_stock(flower, qty, ref or "手动入库", operator, target_date=str(stock_date))
                    # 获取快照表最新日期
                    from inventory_service import get_latest_snapshot_date, get_inventory_snapshot

                    latest_date = get_latest_snapshot_date()
                    if latest_date:
                        # 获取该花型在最新日期的快照
                        df_snap = get_inventory_snapshot(latest_date, flower=flower)
                        if not df_snap.empty:
                            new_snapshot_stock = df_snap.loc[0, '库存']
                            st.toast(f"✅ {flower} 入库 {qty} 米成功！当前库存（快照）：{new_snapshot_stock:.1f} 米")
                        else:
                            st.toast(f"✅ {flower} 入库 {qty} 米成功！")
                    else:
                        st.toast(f"✅ {flower} 入库 {qty} 米成功！")
                    st.balloons()
                except Exception as e:
                    st.error(f"❌ 入库失败：{e}")


# ============================
# 3. 出库登记
# ============================

elif menu == "📤 出库登记":
    st.header("📤 出库登记（手动扣减/线下销售）")
    st.caption("💡 库存不足时仍可出库，库存最低保持为 0（不会出现负数）")

    with st.form("deduct_stock_form"):
        col1, col2 = st.columns(2)
        with col1:
            flower_list = get_flower_list()
            flower = st.selectbox(
                "花型名称 *",
                options=flower_list,
                placeholder="输入花型名称搜索...",
                help="支持模糊搜索，输入关键词自动过滤",
                index=None,
            )
            qty = st.number_input("出库米数 *", min_value=0.5, step=0.5, format="%.1f")
        with col2:
            ref_date = st.date_input("销售日期", value=date.today(), help="出库日期，该日期及之后的库存都会减少")
            operator = st.text_input("操作人", value="admin")

        submitted = st.form_submit_button("✅ 确认出库")

        if submitted:
            if not flower or qty <= 0:
                st.error("请填写完整信息（花型和米数必填）")
            else:
                try:
                    deduct_stock(flower, qty, f"手工出库 {ref_date}", operator, str(ref_date))

                    # 🔧 从快照表获取最新库存（与库存管理页面一致）
                    from inventory_service import get_latest_snapshot_date, get_inventory_snapshot

                    latest_date = get_latest_snapshot_date()
                    if latest_date:
                        df_snap = get_inventory_snapshot(latest_date, flower=flower)
                        if not df_snap.empty:
                            new_snapshot_stock = df_snap.iloc[0]['库存']
                            st.toast(f"✅ {flower} 出库 {qty} 米成功！（最新库存：{new_snapshot_stock:.1f} 米）")
                        else:
                            st.toast(f"✅ {flower} 出库 {qty} 米成功！")
                    else:
                        st.toast(f"✅ {flower} 出库 {qty} 米成功！")

                    st.balloons()
                except Exception as e:
                    st.error(f"❌ 出库失败：{e}")

# ============================
# 4. 库存流水
# ============================
elif menu == "📋 库存流水":
    st.header("📋 库存流水查询")

    col1, col2 = st.columns(2)
    with col1:
        # 🔧 修改：获取所有花型（含已删除）用于流水查询
        from add_del_flower import get_all_flowers

        all_flowers_df = get_all_flowers(include_deleted=True)
        all_flower_list = all_flowers_df['flower'].tolist()

        flower_options = ["（全部）"] + all_flower_list
        flower_filter = st.selectbox(
            "按花型筛选",
            options=flower_options,
            placeholder="输入花型名称搜索...",
            help="支持模糊搜索，选择具体花型查看流水（含已删除花型），或选择「全部」",
            index=None,
        )
    with col2:
        days = st.number_input("查询最近天数", min_value=1, max_value=90, value=30)

    if st.button("🔍 查询"):
        actual_flower = None if flower_filter == "（全部）" else flower_filter
        df = get_stock_log(actual_flower, days)

        if df.empty:
            st.info("没有找到相关流水记录")
        else:
            # 定义变动类型映射
            type_map = {
                '初始化': '🔄 初始化',
                '入库': '📥 入库',
                '销售出库': '📤 销售出库',
                '报损': '⚠️ 报损',
                '盘点调整': '📊 盘点调整',
                '手动调整': '✏️ 手动调整',
                '新增花型': '➕ 新增花型',
                '删除花型': '🗑️ 删除花型',
                '恢复花型': '♻️ 恢复花型'
            }

            if '变动类型' in df.columns:
                df['变动类型'] = df['变动类型'].map(type_map).fillna(df['变动类型'])

            st.dataframe(df, use_container_width=True)


elif menu == "🚨 预警中心":
    st.header("🚨 补货预警 & 拿货建议")
    st.caption("💡 基于近7天销量和当前库存，为所有花型提供拿货建议，按紧急程度排序")

    from inventory_service import get_inventory_report, get_latest_snapshot_date, get_restock_suggestions
    from add_del_flower import get_available_flowers

    latest_date = get_latest_snapshot_date()
    if not latest_date:
        st.info("暂无库存数据")
    else:
        available_df = get_available_flowers()
        available_flowers = set(available_df['flower'].tolist())

        if not available_flowers:
            st.info("当前没有可用的花型")
        else:
            inv_df = get_inventory_report()

            # 只保留可用花型（未删除的）
            inv_df = inv_df[inv_df['花型'].isin(available_flowers)]

            if inv_df.empty:
                st.info("当前没有可用的花型数据")
            else:
                # ==================== 拿货建议（所有花型） ====================
                st.subheader("📦 拿货建议（按紧急程度排序）")

                # 可配置拿货目标天数
                col_target, col_spacer = st.columns([1, 3])
                with col_target:
                    target_days = st.number_input(
                        "拿货目标天数",
                        min_value=1,
                        max_value=30,
                        value=7,
                        step=1,
                        help="建议拿够多少天的库存。例：7 表示建议拿货到够卖 7 天"
                    )

                suggestions_df = get_restock_suggestions(target_days=target_days)

                if suggestions_df.empty:
                    st.info("暂无花型数据")
                else:
                    # 汇总统计
                    total_suggest = suggestions_df['建议拿货(米)'].sum()
                    need_restock = suggestions_df[suggestions_df['建议拿货(米)'] > 0]
                    no_need = suggestions_df[suggestions_df['建议拿货(米)'] == 0]

                    col1, col2, col3, col4 = st.columns(4)
                    with col1:
                        st.metric("🌸 花型总数", f"{len(suggestions_df)} 个")
                    with col2:
                        st.metric("📦 建议总拿货量", f"{total_suggest:.1f} 米")
                    with col3:
                        st.metric("🔴 需要拿货", f"{len(need_restock)} 个花型",
                                  delta=f"共 {need_restock['建议拿货(米)'].sum():.1f} 米" if len(need_restock) > 0 else None)
                    with col4:
                        st.metric("🟢 库存充足", f"{len(no_need)} 个花型")

                    st.divider()

                    # 表格样式：根据紧急程度着色
                    def color_suggestions(row):
                        days = row['可售天数']
                        if days is None:
                            # 无销量，灰色
                            return ['color: #999'] * len(row)
                        elif days <= 1:
                            # 极度紧急，深红
                            return ['background-color: #ff6b6b; color: white'] * len(row)
                        elif days <= 3:
                            # 紧急，浅红
                            return ['background-color: #ffcccc'] * len(row)
                        elif days <= 7:
                            # 关注，浅黄
                            return ['background-color: #fff3cd'] * len(row)
                        elif row['建议拿货(米)'] > 0:
                            # 有一些建议但不太紧急
                            return ['background-color: #e8f5e9'] * len(row)
                        else:
                            # 库存充足，正常
                            return [''] * len(row)

                    # 格式化显示：可售天数 None → "-"
                    display_df = suggestions_df.copy()
                    display_df['可售天数'] = display_df['可售天数'].apply(
                        lambda x: "-" if x is None else x
                    )

                    st.dataframe(
                        display_df.style.apply(color_suggestions, axis=1),
                        use_container_width=True,
                        height=500
                    )

                    st.caption(
                        "🔴 深红 = 可售 ≤1天（立即拿货） | "
                        "🟠 浅红 = 可售 ≤3天 | "
                        "🟡 浅黄 = 可售 ≤7天 | "
                        "🟢 浅绿 = 需要拿货但不紧急 | "
                        "⬜ 白色 = 库存充足 | "
                        "⬜ 灰色 = 无销量"
                    )

                st.divider()

                # ==================== 原有预警列表 ====================
                st.subheader("⚠️ 库存预警明细（低于预警天数）")

                with engine.connect() as conn:
                    avg_sales = pd.read_sql(
                        text("""
                             SELECT flower, AVG(total_meters) as avg_daily
                             FROM daily_report_cache
                             WHERE report_date >= DATE_SUB(CURDATE(), INTERVAL 7 DAY)
                             GROUP BY flower
                             """),
                        conn
                    )
                avg_map = dict(zip(avg_sales['flower'], avg_sales['avg_daily']))
                inv_df['日均销量'] = inv_df['花型'].apply(lambda x: round(avg_map.get(x, 0), 2))
                inv_df['可售天数'] = inv_df.apply(
                    lambda row: round(row['当前库存(米)'] / row['日均销量'], 1) if row['日均销量'] > 0 else float(
                        'inf'),
                    axis=1
                )
                alert_df = inv_df[
                    (inv_df['可售天数'] != float('inf')) &
                    (inv_df['可售天数'] < inv_df['预警天数'])
                    ]

                if not alert_df.empty:
                    st.dataframe(
                        alert_df[['花型', '当前库存(米)', '日均销量', '可售天数', '预警天数']],
                        use_container_width=True
                    )
                    st.caption(f"📊 当前监控花型数：{len(inv_df)} 个（已排除已删除花型）")
                else:
                    st.success("✅ 所有花型库存充足，暂无补货预警")
                    st.caption(f"📊 当前监控花型数：{len(inv_df)} 个（已排除已删除花型）")

elif menu == "📊 报告生成":
    st.header("📊 报告生成")

    # ---------- 在线生成日报 ----------
    st.subheader("📝 生成日报")
    st.info("💡 每次生成都会强制覆盖已有日报，先回退旧库存，再基于最新订单数据重新扣减")

    col1, col2 = st.columns([3, 1])
    with col1:
        gen_date = st.date_input("选择日期", value=date.today())
    with col2:
        gen_btn = st.button("🚀 生成日报", type="primary", use_container_width=True)

    if gen_btn:
        from make_daily import generate_daily_report

        date_str = gen_date.strftime("%Y-%m-%d")
        with st.spinner(f"正在生成 {date_str} 的日报（会先回退旧库存）..."):
            try:
                filepath = generate_daily_report(date_str, force=True)
                if filepath and os.path.exists(filepath):
                    st.success(f"✅ {date_str} 日报生成成功！")
                    # 提供下载按钮
                    with open(filepath, "rb") as f:
                        st.download_button(
                            label="📥 下载日报 Excel",
                            data=f,
                            file_name=os.path.basename(filepath),
                            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                        )
                else:
                    st.warning(f"生成完成，但未找到文件，请检查目录：{filepath}")
            except Exception as e:
                st.error(f"❌ 生成失败：{e}")

    st.divider()

    # ---------- 生成区间报告 ----------
    st.subheader("📅 生成区间报告")
    st.info("💡 生成指定日期范围内的汇总报表（口径与日报一致：只要发货/收货且不退款即计入，仅排除退款与已取消订单）")

    col1, col2 = st.columns(2)
    with col1:
        range_start = st.date_input("开始日期", value=date(2026, 7, 1))
    with col2:
        range_end = st.date_input("结束日期", value=date.today())

    if st.button("🚀 生成区间报告", type="primary"):
        from make_daily import generate_range_report

        start_str = range_start.strftime("%Y-%m-%d")
        end_str = range_end.strftime("%Y-%m-%d")
        with st.spinner(f"正在生成 {start_str} 至 {end_str} 的区间报告..."):
            try:
                filepath = generate_range_report(start_str, end_str, force=True)
                if filepath and os.path.exists(filepath):
                    st.success("✅ 区间报告生成成功！")
                    with open(filepath, "rb") as f:
                        st.download_button(
                            label="📥 下载区间报告 Excel",
                            data=f,
                            file_name=os.path.basename(filepath),
                            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                        )
                else:
                    st.warning(f"生成完成，但未找到文件，请检查目录：{filepath}")
            except Exception as e:
                st.error(f"❌ 生成失败：{e}")

    st.divider()

    # ---------- 出库入库明细 ----------
    st.subheader("📦 出库入库明细")
    st.info("💡 按日期范围生成每个花型的每日出库/入库明细（期初库存 = 开始日期前一天快照，入库按生效日期归集）")

    col1, col2 = st.columns(2)
    with col1:
        inout_start = st.date_input("开始日期", value=date.today() - timedelta(days=30),key="inout_start_date" )
    with col2:
        inout_end = st.date_input("结束日期", value=date.today(),key="inout_end_date" )

    if st.button("🚀 生成报表", type="primary"):
        from make_report import generate_inout_detail_report

        with st.spinner(f"正在生成 {inout_start} 至 {inout_end} 的出库入库明细..."):
            try:
                filepath = generate_inout_detail_report(inout_start, inout_end)
                if filepath and os.path.exists(filepath):
                    st.success("✅ 出库入库明细生成成功！")
                    with open(filepath, "rb") as f:
                        st.download_button(
                            label="📥 下载出库入库明细 Excel",
                            data=f,
                            file_name=os.path.basename(filepath),
                            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                            key="download_inout_btn"
                        )
                else:
                    st.warning(f"生成完成，但未找到文件，请检查目录：{filepath}")
            except ValueError as e:
                st.error(f"❌ {e}")
            except Exception as e:
                st.error(f"❌ 生成失败：{e}")


elif menu == "⚙️ 库存调整":
    st.header("⚙️ 库存直接调整")
    with st.form("adjust_stock_form"):
        col1, col2 = st.columns(2)
        with col1:
            flower_list = get_flower_list()
            flower = st.selectbox(
                "选择花型 *",
                options=flower_list,
                placeholder="输入花型名称搜索...",
                index=None,
            )
        with col2:
            # 如果选择了花型，显示当前库存
            current_stock = 0
            if flower:
                try:
                    from inventory_service import get_current_stock

                    current_stock = get_current_stock(flower)
                except:
                    pass
            st.metric("当前库存", f"{current_stock} 米")

            target_stock = st.number_input(
                "目标库存（米）*",
                min_value=0.0,
                step=0.5,
                format="%.1f",
                value=float(current_stock)
            )

        col3, col4 = st.columns(2)
        with col3:
            ref = st.text_input("调整原因（备注）", placeholder="例：盘点调整 / 系统错误修正")
        with col4:
            operator = st.text_input("操作人", value="admin")

        submitted = st.form_submit_button("✅ 确认调整", type="primary")

        if submitted:
            if not flower:
                st.error("请选择花型")
            elif target_stock < 0:
                st.error("目标库存不能为负数")
            else:
                try:
                    from inventory_service import adjust_stock

                    old, new, diff, msg = adjust_stock(
                        flower=flower,
                        target_stock=target_stock,
                        reference=ref or "手动调整",
                        operator=operator
                    )
                    if diff == 0:
                        st.info("⚠️ 库存没有变化，无需调整")
                    else:
                        st.success(msg)
                        st.balloons()
                        # 延迟刷新显示新库存
                        time.sleep(1)
                        st.rerun()
                except Exception as e:
                    st.error(f"❌ 调整失败：{e}")

    st.markdown("---")
elif menu == "📊 退款明细":
    st.header("📊 退款明细表")
    st.info("💡 显示所有已发货且退款成功的订单明细（按退款时间倒序）")

    # 查询退款明细
    with engine.connect() as conn:
        # 获取所有花型（用于筛选）
        flowers = conn.execute(
            text("SELECT DISTINCT flower FROM refund_detail WHERE flower IS NOT NULL ORDER BY flower")
        ).fetchall()
        flower_list = ["（全部）"] + [row[0] for row in flowers]

        # 日期筛选器
        col1, col2, col3 = st.columns([2, 2, 1])
        with col1:
            start_date = st.date_input("开始日期", value=date(2026, 7, 1))
        with col2:
            end_date = st.date_input("结束日期", value=date.today())
        with col3:
            selected_flower = st.selectbox("花型筛选", options=flower_list, index=0)

        # 构建查询条件
        conditions = ["refund_time >= :start", "refund_time <= :end"]
        params = {"start": str(start_date), "end": str(end_date)}
        if selected_flower != "（全部）":
            conditions.append("flower = :flower")
            params["flower"] = selected_flower

        where_clause = " AND ".join(conditions)
        query = text(f"""
                SELECT 
                    order_no AS '订单号',
                    flower AS '花型',
                    product_spec AS '商品规格',
                    product_quantity AS '数量',
                    refund_meters AS '退款米数',
                    refund_amount AS '退款金额',
                    after_sale_status AS '售后状态',
                    refund_time AS '退款时间'
                FROM refund_detail
                WHERE {where_clause}
                ORDER BY refund_time DESC
            """)

        df = pd.read_sql(query, conn, params=params)

        # 🔧 查询总营业额（排除所有退款和取消订单）
        total_revenue_query = text("""
                SELECT SUM(merchant_income) 
                FROM data2026 
                WHERE DATE(delivery_time) >= :start 
                  AND DATE(delivery_time) <= :end
                  AND order_status != '已取消'
                  AND after_sale_status NOT LIKE '%退款成功%'
            """)
        total_revenue = conn.execute(
            total_revenue_query,
            {"start": start_date, "end": end_date}
        ).scalar() or 0

        # 🔧 转为 float（避免 Decimal 与 float 运算报错）
        total_revenue = float(total_revenue)

    # 显示统计
    if not df.empty:
        st.subheader(f"📊 共找到 {len(df)} 条退款记录")
        total_meters = df['退款米数'].sum()
        total_amount = df['退款金额'].sum()
        refund_rate = (total_amount / total_revenue * 100) if total_revenue > 0 else 0

        st.metric("总退款米数", f"{total_meters:.2f} 米")
        st.metric("总退款金额", f"{total_amount:.2f} 元",
                      delta=f"占营业额 {refund_rate:.1f}%")

        # 显示表格
        st.dataframe(df, use_container_width=True, height=500)

        # 下载按钮
        csv = df.to_csv(index=False, encoding='utf-8-sig')
        st.download_button(
            label="📥 下载 CSV",
            data=csv,
            file_name=f"退款明细_{start_date}_{end_date}.csv",
            mime="text/csv"
        )
    else:
        st.info("没有找到符合条件的退款记录")

elif menu == "📦 库存管理":
    st.header("📦 库存管理")
    st.caption("查看任意日期的库存，修改某天花型库存（自动联动后续日期），或回退到该天")

    from inventory_service import (
        get_inventory_snapshot, get_latest_snapshot_date,
        update_inventory_snapshot, rollback_inventory_to_date
    )
    from system_service import get_system_start_date
    from mysql_conn import engine
    from sqlalchemy import text

    start_date = get_system_start_date()
    max_date = get_latest_snapshot_date() or date.today()
    default_date = max_date if max_date > start_date else start_date

    # ============================================================
    # 顶部：日期选择 + 回退按钮
    # ============================================================
    col1, col2, col3 = st.columns([3, 1, 1])
    with col1:
        target_date = st.date_input(
            "📅 选择日期",
            value=default_date,
            max_value=date.today(),
            key="inventory_date"
        )
    with col2:
        if st.button("🔄 回退到该天", use_container_width=True, type="secondary"):
            st.session_state['rollback_date'] = target_date
            st.session_state['show_rollback_confirm'] = True
    with col3:
        if st.button("🔄 刷新", use_container_width=True):
            st.rerun()

    # ============================================================
    # 回退确认对话框
    # ============================================================
    if st.session_state.get('show_rollback_confirm', False):
        rollback_date = st.session_state['rollback_date']
        st.warning(f"⚠️ 您确认要将库存回退到 **{rollback_date.strftime('%Y-%m-%d')}** 吗？")
        st.caption("此操作将删除该日期之后的所有快照，库存将恢复到该日期的状态，无法直接撤销！")

        col_confirm, col_cancel = st.columns(2)
        with col_confirm:
            if st.button("✅ 确认回退", type="primary", use_container_width=True):
                with st.spinner(f"正在回退到 {rollback_date.strftime('%Y-%m-%d')}..."):
                    success, msg, count = rollback_inventory_to_date(
                        target_date=rollback_date.strftime('%Y-%m-%d'),
                        operator='web'
                    )
                    if success:
                        st.success(msg)
                        st.balloons()
                        st.session_state['show_rollback_confirm'] = False
                        st.rerun()
                    else:
                        st.error(msg)
                        st.session_state['show_rollback_confirm'] = False
        with col_cancel:
            if st.button("❌ 取消", use_container_width=True):
                st.session_state['show_rollback_confirm'] = False
                st.rerun()

        st.divider()

    # ============================================================
    # 获取该日库存数据
    # ============================================================
    df = get_inventory_snapshot(target_date)
    if df.empty:
        st.warning(f"⚠️ {target_date} 没有库存数据，请先生成日报或补全快照")
    else:
        # ============================================================
        # 计算近7天日均销量和可售天数
        # ============================================================
        with engine.connect() as conn:
            avg_sales = pd.read_sql(
                text("""
                    SELECT flower, AVG(total_meters) as avg_daily
                    FROM daily_report_cache
                    WHERE report_date >= DATE_SUB(:target_date, INTERVAL 7 DAY)
                      AND report_date <= :target_date
                    GROUP BY flower
                """),
                conn,
                params={"target_date": target_date}
            )
        avg_map = dict(zip(avg_sales['flower'], avg_sales['avg_daily']))

        df['近7天日均销量'] = df['花型'].apply(
            lambda x: round(avg_map.get(x, 0), 2)
        )
        df['可售天数'] = df.apply(
            lambda row: round(row['库存'] / row['近7天日均销量'], 1)
            if row['近7天日均销量'] > 0 else float('inf'),
            axis=1
        )


        def color_rows(row):
            if row['可售天数'] != float('inf') and row['可售天数'] < 7:
                return ['background-color: #ffcccc'] * len(row)
            return [''] * len(row)


        # ============================================================
        # 显示统计信息和表格
        # ============================================================
        st.subheader(f"📊 {target_date} 的库存（共 {len(df)} 个花型）")

        total_stock = df['库存'].sum()
        col_stat1, col_stat2, col_stat3 = st.columns(3)
        with col_stat1:
            st.metric("合计库存", f"{total_stock:.2f} 米")
        with col_stat2:
            low_stock_count = len(df[(df['可售天数'] != float('inf')) & (df['可售天数'] < 7)])
            st.metric("⚠️ 低于预警阈值", f"{low_stock_count} 个花型", delta="需补货" if low_stock_count > 0 else None)
        with col_stat3:
            no_sales_count = len(df[df['近7天日均销量'] == 0])
            st.metric("📊 无销量花型", f"{no_sales_count} 个")

        st.dataframe(
            df.style.apply(color_rows, axis=1),
            use_container_width=True,
            height=400
        )
        st.caption("🔴 红色行 = 可售天数低于7天，建议补货")

        # ============================================================
        # 修改库存表单
        # ============================================================
        st.divider()
        st.subheader("✏️ 修改该日库存")

        flower = st.selectbox(
            "选择花型",
            options=df['花型'].tolist(),
            index=None,
            placeholder="请选择花型...",
            key="modify_flower"
        )

        if flower is not None:
            current_stock = float(df[df['花型'] == flower]['库存'].iloc[0])
            current_avg = float(df[df['花型'] == flower]['近7天日均销量'].iloc[0])

            with st.form("modify_snapshot"):
                col1, col2 = st.columns(2)
                with col1:
                    new_stock = st.number_input(
                        "新库存（米）",
                        min_value=0.0,
                        step=0.5,
                        value=None,
                        placeholder=f"当前库存：{current_stock} 米"
                    )
                    st.caption(f"📌 当前库存：**{current_stock}** 米 | 日均销量：**{current_avg}** 米")
                with col2:
                    reason = st.text_input("修改原因", placeholder="例：盘点调整")

                submitted = st.form_submit_button("✅ 确认修改", use_container_width=True)

                if submitted:
                    if new_stock is None or new_stock < 0:
                        st.error("❌ 请输入有效的库存米数")
                    elif abs(new_stock - current_stock) < 0.001:
                        st.info("库存无变化，无需修改")
                    else:
                        success, msg, affected = update_inventory_snapshot(
                            flower=flower,
                            target_date=target_date,
                            new_stock=new_stock,
                            operator="web",
                            reason=reason or "手动调整"
                        )
                        if success:
                            st.success(f"{msg}，影响 {affected} 天")
                            st.balloons()
                            st.rerun()
                        else:
                            st.error(msg)
        else:
            st.info("👆 请先选择一个花型")
elif menu == "⚙️ 系统设置":
    st.header("⚙️ 系统设置")

    from system_service import get_system_start_date, set_system_start_date
    from inventory_service import get_latest_snapshot_date, fill_missing_snapshots

    current_start_date = get_system_start_date()
    latest_snapshot = get_latest_snapshot_date()

    st.subheader("📅 系统起始日期")
    st.info(f"当前起始日期：**{current_start_date.strftime('%Y-%m-%d')}**")
    st.caption("所有数据（库存、订单、日报）将只显示此日期之后的内容")

    col1, col2 = st.columns([2, 1])
    with col1:
        new_start_date = st.date_input(
            "选择新的起始日期",
            value=current_start_date,
            max_value=datetime.now().date()
        )
    with col2:
        st.write("")
        st.write("")
        if st.button("✅ 确认修改起始日期", type="primary", use_container_width=True):
            if new_start_date > datetime.now().date():
                st.error("❌ 起始日期不能晚于今天")
            else:
                # 确认修改
                confirm = st.checkbox("我确认修改起始日期，系统将重新生成快照")
                if confirm:
                    with st.spinner("正在修改起始日期并重新生成快照..."):
                        # 1. 更新配置
                        set_system_start_date(new_start_date)

                        # 2. 清空快照并重新初始化
                        with engine.connect() as conn:
                            conn.execute(text("TRUNCATE TABLE inventory_snapshot"))
                            conn.commit()

                        # 3. 🔧 修正：重新生成快照，并处理返回值
                        filled, fill_status, msg = fill_missing_snapshots(operator="system")

                        if fill_status == "no_report":
                            st.warning(f"✅ 起始日期已更新为 {new_start_date.strftime('%Y-%m-%d')}，但 {msg}")
                        elif fill_status == "already_latest":
                            st.info(f"✅ 起始日期已更新为 {new_start_date.strftime('%Y-%m-%d')}，{msg}")
                        elif fill_status == "error":
                            st.error(f"❌ 起始日期已更新，但快照生成失败：{msg}")
                        else:
                            st.success(f"✅ 起始日期已更新为 {new_start_date.strftime('%Y-%m-%d')}，{msg}")
                            st.balloons()

                        st.rerun()
                else:
                    st.warning("⚠️ 请先勾选确认复选框")

    st.divider()

    # 显示当前数据范围
    st.subheader("📊 当前数据范围")
    with engine.connect() as conn:
        # 订单日期范围（起始日期之后）
        order_range = conn.execute(
            text("""
                SELECT MIN(DATE(delivery_time)), MAX(DATE(delivery_time))
                FROM data2026 
                WHERE delivery_time IS NOT NULL 
                  AND DATE(delivery_time) >= :start
            """),
            {"start": current_start_date}
        ).fetchone()

        # 日报日期范围
        report_range = conn.execute(
            text("""
                SELECT MIN(report_date), MAX(report_date) 
                FROM daily_report_cache 
                WHERE report_date >= :start
            """),
            {"start": current_start_date}
        ).fetchone()

        # 快照日期范围
        snapshot_range = conn.execute(
            text("""
                SELECT MIN(snapshot_date), MAX(snapshot_date) 
                FROM inventory_snapshot 
                WHERE snapshot_date >= :start
            """),
            {"start": current_start_date}
        ).fetchone()

    col1, col2, col3 = st.columns(3)
    with col1:
        st.metric(
            "📋 订单数据",
            f"{order_range[0].strftime('%Y-%m-%d') if order_range[0] else '无'} ~ {order_range[1].strftime('%Y-%m-%d') if order_range[1] else '无'}"
        )
    with col2:
        st.metric(
            "📊 日报数据",
            f"{report_range[0].strftime('%Y-%m-%d') if report_range[0] else '无'} ~ {report_range[1].strftime('%Y-%m-%d') if report_range[1] else '无'}"
        )
    with col3:
        st.metric(
            "📸 库存快照",
            f"{snapshot_range[0].strftime('%Y-%m-%d') if snapshot_range[0] else '无'} ~ {snapshot_range[1].strftime('%Y-%m-%d') if snapshot_range[1] else '无'}"
        )
elif menu == "🌸 花型管理":
    st.header("🌸 花型管理")
    st.caption("支持新增花型、软删除花型（历史数据保留）、恢复已删除花型")

    from add_del_flower import get_all_flowers, add_flower, delete_flower, restore_flower

    # ============= 顶部：新增花型表单 =============
    st.subheader("➕ 新增花型")
    with st.form("add_flower_form"):
        col1, col2, col3 = st.columns([2, 1, 1])
        with col1:
            new_flower_name = st.text_input("花型名称 *", placeholder="请输入花型名称")
        with col2:
            new_cost = st.number_input("成本单价（元/米）", min_value=0.0, step=0.1, format="%.2f", value=0.0)
        with col3:
            operator_add = st.text_input("操作人", value="admin")
        submitted_add = st.form_submit_button("✅ 确认新增", type="primary")

        if submitted_add:
            if not new_flower_name.strip():
                st.error("❌ 请输入花型名称")
            else:
                success, msg = add_flower(new_flower_name.strip(), new_cost, operator_add)
                if success:
                    st.success(msg)
                    st.balloons()
                    st.cache_data.clear()
                    time.sleep(1)
                    st.rerun()
                else:
                    st.error(msg)

    st.divider()

    # ============= 底部：花型列表 =============
    st.subheader("📋 花型列表")
    flowers_df = get_all_flowers(include_deleted=True)

    if flowers_df.empty:
        st.info("暂无花型数据")
    else:
        display_df = flowers_df.copy()
        display_df['状态'] = display_df['is_deleted'].apply(lambda x: '已删除' if x == 1 else '正常')
        display_df = display_df.rename(columns={
            'flower': '花型名称',
            'cost_per_meter': '成本单价(元/米)',
            'delete_effect_date': '删除生效日期'
        })
        # 样式：已删除的行置灰
        def style_deleted(row):
            if row['状态'] == '已删除':
                return ['color: #999999; background-color: #f5f5f5'] * len(row)
            return [''] * len(row)


        display_df = flowers_df.copy()
        display_df['状态'] = display_df['is_deleted'].apply(lambda x: '已删除' if x == 1 else '正常')
        display_df = display_df.rename(columns={
            'flower': '花型名称',
            'cost_per_meter': '成本单价(元/米)'
        })

        st.dataframe(
            display_df[['花型名称', '成本单价(元/米)', '状态', 'delete_time']].style.apply(style_deleted, axis=1),
            use_container_width=True,
            height=400
        )
        st.caption("💡 灰色行 = 已删除花型，历史数据保留，不参与业务下拉选择")

        st.divider()

        # 删除/恢复操作区
        st.subheader("⚙️ 删除 / 恢复")
        col_op1, col_op2, col_op3 = st.columns([2, 1, 1])
        with col_op1:
            all_names = flowers_df['flower'].tolist()
            selected_flower = st.selectbox(
                "选择花型",
                options=all_names,
                index=None,
                placeholder="请选择要操作的花型..."
            )
        with col_op2:
            st.write("")
            st.write("")
            btn_delete = st.button("🗑️ 删除花型", use_container_width=True, type="secondary")
        with col_op3:
            st.write("")
            st.write("")
            btn_restore = st.button("♻️ 恢复花型", use_container_width=True, type="primary")

        if btn_delete:
            if not selected_flower:
                st.warning("请先选择花型")
            else:
                success, msg = delete_flower(selected_flower, "admin")
                if success:
                    st.success(msg)
                    st.cache_data.clear()
                    time.sleep(1)
                    st.rerun()
                else:
                    st.error(msg)

        if btn_restore:
            if not selected_flower:
                st.warning("请先选择花型")
            else:
                success, msg = restore_flower(selected_flower, "admin")
                if success:
                    st.success(msg)
                    st.cache_data.clear()
                    time.sleep(1)
                    st.rerun()
                else:
                    st.error(msg)
# ============================
# 启动入口
# ============================
if __name__ == "__main__":
    pass