```
# 🧵 布料店库存管理系统

基于 Streamlit + MySQL 的布料店库存管理工具，支持订单导入、日报/月报生成、库存快照追踪、库存时间线回退、预警等功能。



## ✨ 主要功能

- **订单导入**：支持上传拼多多 / 淘宝导出的 Excel 订单，自动识别平台、清洗并 UPSERT 到数据库（平台字段可扩展，已预留抖音）
- **日报/月报生成**：按日期生成销售日报和月度汇总报表，日报按平台分组统计，自动计算成本、利润
- **库存快照**：以日期为维度记录每个花型的库存，支持任意日期查询和修改
- **库存时间线**：查看指定日期的库存，修改某天库存后自动联动后续所有日期
- **回退库存**：一键回退到任意历史日期（删除该日之后的所有快照）
- **预警中心**：基于近7天日均销量计算可售天数，低于阈值时高亮提醒
- **系统设置**：可调整数据起始日期，重置基准库存

## 🛠 技术栈

- Python 3.8+
- Streamlit 1.28+
- MySQL 5.7+
- Pandas / SQLAlchemy / PyMySQL
- OpenPyXL（Excel 读写）

## 📦 安装与部署

### 1. 克隆仓库

​```bash
git clone https://github.com/你的用户名/shop_data_system.git
cd shop_data_system
```

### 2. 创建 Python 虚拟环境（推荐）

```
python -m venv venv
venv\Scripts\activate   # Windows
# source venv/bin/activate  # Linux/Mac
```

### 3. 安装依赖

```
pip install -r requirements.txt
```

### 4. 配置数据库

- 在 MySQL 中创建数据库 `shop_data`（字符集 utf8mb4）
- 修改 `config.py` 中的数据库连接信息：

```
MYSQL_USER = "root"
MYSQL_PASSWORD = "你的密码"
MYSQL_HOST = "localhost"
MYSQL_PORT = "3306"
MYSQL_DATABASE = "shop_data"
```

### 5. 执行建表脚本

在 MySQL 中执行 `schema.sql` 文件：

```
mysql -u root -p shop_data < schema.sql
```

> 若为**已有数据库**升级（启用淘宝导入），需先为 `data2026` 表增加 `platform` 字段
> （0=拼多多，1=淘宝，2=抖音预留）。两种方式任选其一：
> - 一键迁移：运行 `python migrate_platform.py`（幂等，可重复执行）
> - 手动执行 SQL：`mysql -u root -p shop_data < alter_table_add_platform.sql`
>
> 不执行迁移则数据表无 `platform` 列，导入会提示先运行迁移脚本。

### 6. 初始化花型成本表

运行 `make_cost_table.py` 从订单 Excel 中提取花型列表，并导入 `product_cost` 表（需先准备一份订单文件，修改脚本中的路径）。

```
python make_cost_table.py
```

然后手动更新 `product_cost.cost_per_meter` 为实际成本价。

### 7. 初始化基准库存

```
python init_inventory.py
```

默认将所有花型基准库存设为 2000 米，基准日期为 2026-07-01。

### 8. 启动应用

```
streamlit run app.py --server.address 0.0.0.0 --server.port 8501
```

浏览器访问 `http://localhost:8501` 即可。

## 📖 使用指南

### 首次使用

1. **导入历史订单**：在网页“导入订单”页面上传拼多多或淘宝导出的 Excel 文件（自动识别平台）。
2. **导入成本表数据**：可手动导入，也可运行make_cost_table.py文件
3. **初始化库存**：运行init_inventory.py文件，默认初始化所有花型为2000米
4. **生成日报**：在“日报中心”选择日期生成日报（或点击首页“一键生成所有缺失日报”）。
5. **补全库存快照**：在首页点击“补全缺失快照”，系统将基于日报数据生成各日期的库存快照。
6. **查看库存**：在“库存总览”查看最新库存，在“库存时间线”查看任意日期的库存。

### 日常使用流程

每天的工作流程：

1. **导入新订单**：从拼多多导出当天（或前一天）的订单，上传导入
2. **生成日报**：在日报中心选择当天日期，生成日报（快照自动生成）
3. **查看库存**：在库存总览查看最新库存
4. **处理预警**：如有库存不足的花型，安排补货

**日常使用中，快照不需要手动操作，日报生成时自动处理。**

### 常见操作

- **修改某天库存**：进入“库存时间线”→选择日期→选择花型→输入新库存→确认（后续日期自动联动）。
- **回退库存**：在“库存时间线”选择回退日期→点击“回退到该天”→确认（删除该日之后的快照）。
- **调整系统起始日期**：在“系统设置”中修改起始日期，系统将清空快照并重新生成。

## 🔧 其他脚本说明

- `make_daily.py`：命令行生成日报，支持单日或批量（`python make_daily.py 2026-07-01`）。
- `make_monthly.py`：生成指定月份的月报。
- `populate_refund_details.py`：同步退款明细（已发货退款成功的订单）。
- `import_order.py`：命令行导入 Excel（也可在网页上传）。

## 🤝 贡献

欢迎提交 Issue 和 Pull Request。