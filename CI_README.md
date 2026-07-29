# CI 使用说明

## 1. CI 流程概述

本项目使用 **GitHub Actions** 作为持续集成（CI）工具。每次 `git push` 到 `main` 或 `feature/*` 分支，以及每次向 `main` 发起 Pull Request 时，CI 会自动运行。

### CI 流程步骤

1. **检出代码** — 从 GitHub 仓库拉取最新代码
2. **设置 Python** — 在 Python 3.9 / 3.10 / 3.11 三个版本上分别运行
3. **安装依赖** — `pip install -r requirements.txt` + `pytest pytest-cov`
4. **创建临时配置** — 生成占位 `config.py`（CI 环境无真实 MySQL）
5. **运行测试** — `pytest tests/ -v --cov=. --cov-report=term --cov-fail-under=80`
6. **上传覆盖率报告** — 每个 Python 版本的覆盖报告保存为 CI Artifact

### 要求

- 所有测试必须通过
- 测试覆盖率 **不低于 80%**
- 如果 PR 导致测试失败，GitHub 会在 PR 页面显示 ❌，阻止合并

---

## 2. 本地运行测试

### 2.1 前置条件

```bash
# 1. 进入项目目录
cd D:\店铺\shop_data_system

# 2. 创建并激活虚拟环境（推荐）
python -m venv venv
# Windows:
venv\Scripts\activate
# Linux/Mac:
# source venv/bin/activate

# 3. 安装依赖
pip install -r requirements.txt
pip install pytest pytest-cov
```

### 2.2 运行所有测试

```bash
pytest tests/ -v
```

### 2.3 运行测试并查看覆盖率

```bash
pytest tests/ -v --cov=. --cov-report=term
```

### 2.4 运行单个测试文件

```bash
pytest tests/test_extract_flower.py -v
pytest tests/test_inventory_service.py -v --cov=. --cov-report=term
```

### 2.5 运行特定测试类或函数

```bash
pytest tests/test_extract_flower.py::TestExtractFlowerFromSpec -v
pytest tests/test_inventory_service.py::TestCheckFlowerActive -v
```

---

## 3. 测试架构说明

### 3.1 测试文件清单

| 文件 | 类型 | 测试内容 |
|------|------|----------|
| `test_imports.py` | 冒烟测试 | 验证所有模块可正常导入 |
| `test_extract_flower.py` | 单元测试 | `extract_flower_from_spec` 纯函数 |
| `test_extract_meter.py` | 单元测试 | `extract_meter_from_spec` 纯函数 |
| `test_inventory_service.py` | 集成测试 | `check_flower_active`, `add_stock`, `deduct_stock`, `update_inventory_snapshot`, `fill_missing_snapshots`, `get_restock_suggestions` 等 |
| `test_add_del_flower.py` | 集成测试 | 花型增删改查（`add_flower`, `delete_flower`, `restore_flower`） |
| `test_make_daily.py` | 集成测试 | `load_cost_map`, `generate_daily_report`, `generate_all_missing_reports` |
| `test_import_order.py` | 单元+集成 | 数据清洗函数、`import_excel_from_dataframe` |
| `test_system_service.py` | 集成测试 | `get_system_start_date`, `set_system_start_date` |
| `test_app_start.py` | 冒烟测试 | `app.py` 语法检查和模块导入 |
| `test_populate_refund.py` | 集成测试 | `sync_refund_details` |
| `test_write_off.py` | 集成测试 | `write_off_stock` 报损逻辑 |
| `test_adjust_stock.py` | 集成测试 | `adjust_stock` 手动调整 |

### 3.2 Mock 机制

**核心原理**：所有项目模块都依赖 `mysql_conn.engine` 连接 MySQL 数据库。`tests/conftest.py` 在**任何项目模块导入之前**，将 `mysql_conn` 和 `config` 替换为 MagicMock，从而:

- **无需真实 MySQL 数据库**
- **无需 config.py 配置文件**
- **测试速度快**（无网络 IO）

每个测试函数通过 `mock_conn` fixture 获取一个受控的 Mock 数据库连接：

```python
def test_check_flower_active(self, mock_conn):
    # 模拟数据库返回数据
    mock_conn.execute.return_value.fetchone.return_value = (0,)
    ok, msg = check_flower_active('花型A')
    assert ok is True
```

### 3.3 覆盖哪些场景

每个测试覆盖以下场景：
- ✅ **正常流程**（Happy Path）
- ✅ **边界值**（空值、0、负数）
- ✅ **异常流程**（数据不存在、已被删除）
- ✅ **错误处理**（数据库异常、回滚）

---

## 4. 修改测试配置

### 4.1 修改覆盖率阈值

编辑 `.github/workflows/ci.yml`，修改 `--cov-fail-under` 参数：

```yaml
- name: Run tests with coverage and fail if coverage < 80%
  run: |
    pytest tests/ -v --cov=. --cov-report=term --cov-fail-under=80
```

### 4.2 修改测试矩阵

同样在 `.github/workflows/ci.yml` 中修改 `matrix.python-version`：

```yaml
strategy:
  matrix:
    python-version: ["3.9", "3.10", "3.11", "3.12"]  # 添加 3.12
```

### 4.3 添加新的测试文件

1. 在 `tests/` 目录下创建 `test_xxx.py`
2. 使用 `conftest.py` 中提供的 `mock_conn`、`mock_pd_read_sql` 等 fixture
3. 运行 `pytest tests/test_xxx.py -v` 验证
4. CI 会自动发现并运行新测试

### 4.4 跳过特定测试

```bash
# 跳过慢速测试（假设标记了 @pytest.mark.slow）
pytest tests/ -v -m "not slow"

# 只运行单元测试（不运行集成测试）
pytest tests/test_extract_flower.py tests/test_extract_meter.py -v
```

---

## 5. 常见问题 (FAQ)

### Q1: 本地测试通过，但 CI 失败

可能原因：
- **大小写敏感**：Linux (CI) 文件系统大小写敏感，Windows 不敏感。检查 import 路径大小写。
- **编码问题**：确保文件保存为 UTF-8 编码。
- **路径分隔符**：CI 使用 Linux 环境，路径分隔符为 `/`。代码中使用 `os.path.join` 或 `pathlib`。

解决方法：
```bash
# 在本地 Linux 环境测试（如 WSL）
cd /path/to/project
pytest tests/ -v
```

### Q2: 测试报错 "ModuleNotFoundError: No module named 'mysql_conn'"

- `config.py` 被 `.gitignore` 排除，CI 中会自动生成临时 `config.py`
- 本地测试时确保已创建 `config.py`（从 `config.py.template` 复制或参考 `CI_README.md`）
- 如果只是运行测试，`conftest.py` 的 Mock 机制使得 `config.py` 不是必需的

### Q3: 如何为数据库相关函数编写测试？

```python
from unittest.mock import MagicMock, patch

def test_my_function(self, mock_conn):
    # Step 1: 设置 Mock 返回值
    mock_conn.execute.return_value.fetchone.return_value = (预期值,)
    
    # 或者设置多次调用的不同返回值
    mock_conn.execute.side_effect = [
        MagicMock(fetchone=lambda: (val1,)),  # 第一次调用
        MagicMock(fetchone=lambda: (val2,)),  # 第二次调用
    ]
    
    # Step 2: 调用被测试函数
    result = my_function(param)
    
    # Step 3: 断言
    assert result == 预期结果
```

### Q4: 如何测试使用 `pd.read_sql` 的函数？

使用 `mock_pd_read_sql` fixture：

```python
def test_my_query(self, mock_pd_read_sql, mock_conn):
    mock_pd_read_sql.return_value = pd.DataFrame({
        'flower': ['花型A'],
        'cost_per_meter': [10.0],
    })
    result = load_cost_map('2026-07-15')
    assert result == {'花型A': 10.0}
```

### Q5: 如何在 CI 中添加新的 Python 依赖？

```bash
# 1. 添加到 requirements.txt
echo "new-package>=1.0" >> requirements.txt

# 2. 如果是测试专用依赖，CI yml 中单独安装
# .github/workflows/ci.yml 中已包含 pip install pytest pytest-cov
```

### Q6: 覆盖率报告在哪里看？

- CI 运行结束后，在 GitHub Actions 页面点击对应 Workflow Run
- 展开 "Run tests with coverage" 步骤，可以看到终端输出的覆盖率摘要
- `coverage.xml` 文件作为 Artifact 上传，可下载后用 IDE 或 `coverage html` 生成 HTML 报告

---

## 6. 本地生成覆盖率 HTML 报告

```bash
# 运行测试并生成 XML + HTML 报告
pytest tests/ -v --cov=. --cov-report=xml --cov-report=html

# 打开 HTML 报告
# Windows: start htmlcov/index.html
# Mac: open htmlcov/index.html
# Linux: xdg-open htmlcov/index.html
```

---

## 7. 维护者

- 测试架构由 `conftest.py` 驱动，保持 `mock_conn` 和 `mock_pd_read_sql` 两个核心 fixture 的稳定性
- 新增功能时，请同步为关键业务逻辑（库存操作、日报生成、花型管理）添加测试
- 覆盖率目标 ≥ 80%，核心业务函数（如库存扣减、快照补全）需 100% 覆盖
