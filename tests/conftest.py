"""
Shared test fixtures and configuration for the shop_data_system test suite.

All project modules depend on mysql_conn.engine (which requires config.py).
This conftest mocks mysql_conn and config at the module level BEFORE any
project module is imported, allowing tests to run without a real MySQL database.
"""
import sys
import pytest
from unittest.mock import MagicMock, patch
import pandas as pd

# ---------------------------------------------------------------------------
# Mock external dependencies at module level (before any project imports)
# ---------------------------------------------------------------------------
_mock_config = MagicMock(name='config')
_mock_config.MYSQL_USER = "test"
_mock_config.MYSQL_PASSWORD = "test"
_mock_config.MYSQL_HOST = "localhost"
_mock_config.MYSQL_PORT = "3306"
_mock_config.MYSQL_DATABASE = "shop_data"

_mock_mysql_conn = MagicMock(name='mysql_conn')
_mock_engine = MagicMock(name='engine')
_mock_mysql_conn.engine = _mock_engine
_mock_mysql_conn.test_mysql = MagicMock()

sys.modules['config'] = _mock_config
sys.modules['mysql_conn'] = _mock_mysql_conn

# Ensure sqlalchemy.text remains the real one for generating TextClause literals
# (we keep sqlalchemy unmocked; only mysql_conn and config are mocked)
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_conn():
    """Provide a mock DB connection wired into engine.connect() / engine.begin().

    Each test gets a fresh ``conn`` MagicMock.  Calling :func:`add_stock`,
    :func:`deduct_stock`, etc. will route all ``conn.execute(...)`` calls
    through this mock.  Use ``conn.execute.side_effect`` or
    ``conn.execute.return_value`` to control what the queried function sees.
    """
    ctx = MagicMock(name='engine_ctx')
    conn = MagicMock(name='conn')
    ctx.__enter__.return_value = conn
    _mock_engine.connect.return_value = ctx
    _mock_engine.begin.return_value = ctx
    return conn


@pytest.fixture
def mock_pd_read_sql():
    """Temporarily replace ``pandas.read_sql`` with a mock.

    Many service functions load DataFrames via ``pd.read_sql(query, conn)``.
    Because our ``conn`` is a MagicMock, the real ``pd.read_sql`` cannot drive
    it.  Apply this fixture whenever the function under test calls
    ``pd.read_sql``.

    Usage::

        def test_with_dataframe(self, mock_pd_read_sql):
            mock_pd_read_sql.return_value = pd.DataFrame({
                'flower': ['A', 'B'],
                'stock':  [10.0, 20.0],
            })
            # … exercise function
    """
    with patch('pandas.read_sql') as mock:
        yield mock


@pytest.fixture
def sample_flowers():
    """Return a list of dicts representing typical product_cost rows."""
    return [
        {'flower': '花型A', 'cost_per_meter': 10.0, 'is_deleted': 0},
        {'flower': '花型B', 'cost_per_meter': 15.5, 'is_deleted': 0},
        {'flower': '已删除花型', 'cost_per_meter': 8.0, 'is_deleted': 1},
    ]
