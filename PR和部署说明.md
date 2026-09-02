# 拉取请求（PR）说明 + 新电脑部署指引

> 分支：`feature/jiushixixi` → `main`
> 创建 PR 链接（直接打开，点 Create pull request 即可）：
> https://github.com/hunter-bot-cheater/shopping_assist/compare/main...feature/jiushixixi

---

## 一、PR 说明（复制到 PR 描述框）

### 标题
日报/月报/区间统计口径改为「按下单日期」，导入后自动重生成受影响日报

### 正文

**概述**
将日报、月报、区间报告的统计口径从「发货时间」改为「用户下单日期」：拼多多=订单号前6位，淘宝=订单付款时间，抖音=支付完成时间（缺失时兜底到下单/提交时间）。同时修复相关统计与界面问题。

**变更内容**

1. 统计口径切换（核心）
   - 新增 `report_date_logic.py` 统一三平台下单日期口径（SQL 与 Python 双实现保持一致）
     - 拼多多 = 订单号前 6 位（YYMMDD）
     - 淘宝 = 订单付款时间（缺失兜底 order_time）
     - 抖音 = 支付完成时间（缺失兜底 order_time）
   - 日报 / 月报 / 区间报告 / 缺失日报补全 / 受影响日期查询 全部改为按下单日期统计
   - `data2026` 新增 `payment_time` 字段（迁移与回填见「数据库变更」）
2. 明细可见性：日报与区间报告的「订单明细」新增「成交日期」列，体现每笔订单归属当日日报的依据
3. 导入流程：导入订单后自动重新生成受影响日期的日报（未生成过的也会自动生成），并提示哪些天的日报已变化
4. 界面：系统管理页移除「库存一致性」展示板块（一键对齐主表 / 清理回退伪影 / 库存漂移指标），后台函数保留
5. 修复：
   - 淘宝「交易关闭」（未付款）单加入取消排除，避免虚增进报表
   - 日报「回退+重扣」置于同一事务，避免中途失败导致库存虚高
6. 打包的既有改动：补货预警按卷 / EOQ 拿货建议改版、库存流水界面优化、number_input 步进按钮样式修复

**数据库变更**
- `data2026` 新增列 `payment_time DATETIME NULL`（schema.sql 已同步）
- 回填：淘宝 95 条（订单付款时间）、抖音 170 条（支付完成时间）
- 存量数据无需改动，缺失 payment_time 的订单自动兜底到 order_time
- 需重新生成起始日期以来的日报 / 月报 / 区间报告

**测试**
- pytest 198 项全部通过（跳过无 streamlit 环境的 test_app_start）

---

## 二、新电脑拉代码 + 部署指引

### 1. 拉代码
```bash
git clone git@github.com:hunter-bot-cheater/shopping_assist.git
cd shopping_assist
git fetch origin
git checkout feature/jiushixixi
# PR 合并到 main 后，直接 checkout main 拉最新即可
```

### 2. 安装依赖
```bash
pip install -r requirements.txt
# 内容：streamlit>=1.28.0、pandas>=2.0.0、sqlalchemy>=2.0.0、pymysql>=1.1.0、openpyxl>=3.1.0
```

### 3. 配置数据库连接
- 编辑 `mysql_conn.py` / `config.py`，改为新机器的 MySQL 地址、端口、账号、密码、库名（默认 `data2026`）。

### 4. 数据库迁移（本分支相对 main 唯一新增字段：payment_time）

**情况 A：全新空库**
直接执行 `schema.sql`（已含 payment_time 字段），然后导入历史订单 Excel，系统会按下单日期口径自动统计。

**情况 B：已有老库（例如从旧电脑复制/迁移过来的数据）**

① 增加 payment_time 字段（可重复执行，已存在会报错但不影响，跳过即可）：
```sql
ALTER TABLE data2026
ADD COLUMN payment_time DATETIME NULL
COMMENT '订单付款时间/支付完成时间（淘宝/抖音下单日期口径）' AFTER order_time;
```

② 回填淘宝 / 抖音付款时间（可选但推荐：让历史订单归属到准确的付款日）。
把导出文件 `淘宝订单.xlsx`、`7月抖音订单明细.xlsx` 放到脚本同目录后执行以下 Python：
```python
# -*- coding: utf-8 -*-
import pandas as pd
import sqlalchemy
from mysql_conn import engine

# 淘宝：订单付款时间 → payment_time（按订单编号，platform=1）
tb = pd.read_excel('淘宝订单.xlsx')
tb['订单编号'] = tb['订单编号'].astype(str).str.strip()
tb['订单付款时间'] = pd.to_datetime(tb['订单付款时间'], errors='coerce')
n = 0
with engine.begin() as conn:
    for _, r in tb.iterrows():
        if pd.isna(r['订单付款时间']):
            continue
        res = conn.execute(sqlalchemy.text(
            "UPDATE data2026 SET payment_time=:p WHERE order_no=:o AND platform=1"
        ), {"p": r['订单付款时间'], "o": r['订单编号']})
        n += res.rowcount
print(f'淘宝回填 {n} 条')

# 抖音：支付完成时间 → payment_time（按子订单编号=order_no，platform=2）
dy = pd.read_excel('7月抖音订单明细.xlsx')
dy['子订单编号'] = dy['子订单编号'].astype(str).str.strip()
dy['支付完成时间'] = pd.to_datetime(dy['支付完成时间'], errors='coerce')
n = 0
with engine.begin() as conn:
    for _, r in dy.iterrows():
        if pd.isna(r['支付完成时间']):
            continue
        res = conn.execute(sqlalchemy.text(
            "UPDATE data2026 SET payment_time=:p WHERE order_no=:o AND platform=2"
        ), {"p": r['支付完成时间'], "o": r['子订单编号']})
        n += res.rowcount
print(f'抖音回填 {n} 条')
```

> 如果老库是从当前这台电脑直接复制/导出的，payment_time 已经回填好，② 可以跳过，只需确认列存在即可。

### 5. 存量数据是否需要变化
- **订单数据本身：不需要改。** 缺失 payment_time 的订单由代码自动兜底到 order_time，不会丢单；「交易关闭/已关闭」未付款单会被取消状态排除，不会虚增。
- **报表 / 库存快照：需要按新口径重算。** 因为日期归属变了，必须重新生成起始日期以来所有日报（force 重算），重算会级联更新库存快照与日报缓存；之后按需生成月报、区间报告。
- 重新生成日报（在项目目录执行）：
```python
# -*- coding: utf-8 -*-
import sys
sys.stdout.reconfigure(encoding='utf-8')
from make_daily import generate_daily_report
from sqlalchemy import text
from mysql_conn import engine
from report_date_logic import ORDER_DATE_SQL

with engine.connect() as conn:
    dates = [r[0] for r in conn.execute(text(
        f"SELECT DISTINCT order_date FROM (SELECT {ORDER_DATE_SQL} AS order_date FROM data2026) t WHERE order_date IS NOT NULL"
    )).fetchall()]
dates = sorted(dates)
for d in dates:
    print(f'生成 {d} ...')
    generate_daily_report(d.strftime('%Y-%m-%d'), force=True)
print(f'共重新生成 {len(dates)} 天日报')
```
- 无需删除任何数据。

### 6. 启动应用
```bash
streamlit run app.py
```
