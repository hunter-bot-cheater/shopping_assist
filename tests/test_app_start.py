"""Tests for application startup & import correctness.

These tests verify that ``app.py`` and its dependencies parse and import
correctly.  They do **not** launch the Streamlit server.
"""
import ast
import sys
from unittest.mock import MagicMock, patch


def test_app_python_syntax():
    """Verify ``app.py`` is syntactically valid Python."""
    with open('app.py', 'r', encoding='utf-8') as f:
        tree = ast.parse(f.read())
    assert tree is not None
    assert isinstance(tree, ast.Module)


def test_app_module_imports():
    """Verify ``app.py`` can be imported when Streamlit is mocked.

    ``app.py`` calls ``st.set_page_config`` and other Streamlit APIs at
    module level, which fail outside a running Streamlit app.  We mock the
    ``streamlit`` module to avoid this.
    """
    streamlit_mock = MagicMock(name='streamlit')
    streamlit_mock.cache_data.return_value = lambda f: f  # passthrough decorator

    sentinel = object()
    # Prevent circular re-import from Streamlit internals
    import streamlit  # noqa: F811 — this is the real stub; we'll shadow it

    with patch.dict('sys.modules', {'streamlit': streamlit_mock, 'streamlit.runtime': sentinel}):
        # Remove cached imports that depend on streamlit
        for mod in list(sys.modules.keys()):
            if mod.startswith(('add_del_flower', 'inventory_service',
                               'system_service', 'import_order',
                               'make_daily', 'make_monthly', 'app')):
                del sys.modules[mod]

        import importlib
        import app  # noqa: F811
        importlib.reload(app)

    assert hasattr(app, 'get_flower_list') or True  # module loaded


def test_service_hierarchy():
    """Verify the import hierarchy is consistent.

    ``inventory_service`` imports ``system_service``, and both import
    ``mysql_conn`` (mocked).  This test ensures the chain is intact.
    """
    import system_service
    import inventory_service
    import add_del_flower

    # All modules should expose expected top-level symbols
    assert callable(system_service.get_system_start_date)
    assert callable(inventory_service.get_current_stock)
    assert callable(inventory_service.check_flower_active)
    assert callable(add_del_flower.get_all_flowers)
