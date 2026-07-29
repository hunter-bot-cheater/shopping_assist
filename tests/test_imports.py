"""Verify that every project module can be imported without errors.

Because ``conftest.py`` already mocks ``mysql_conn`` and ``config`` in
``sys.modules``, these imports do **not** require a real MySQL database
or ``config.py`` on disk.
"""
# pylint: disable=import-outside-toplevel


def test_import_system_service():
    import system_service
    assert hasattr(system_service, 'get_system_start_date')
    assert hasattr(system_service, 'set_system_start_date')
    assert hasattr(system_service, 'get_date_range_for_display')


def test_import_add_del_flower():
    import add_del_flower
    assert hasattr(add_del_flower, 'get_all_flowers')
    assert hasattr(add_del_flower, 'get_available_flowers')
    assert hasattr(add_del_flower, 'add_flower')
    assert hasattr(add_del_flower, 'delete_flower')
    assert hasattr(add_del_flower, 'restore_flower')


def test_import_inventory_service():
    import inventory_service
    assert hasattr(inventory_service, 'get_current_stock')
    assert hasattr(inventory_service, 'add_stock')
    assert hasattr(inventory_service, 'deduct_stock')
    assert hasattr(inventory_service, 'check_flower_active')
    assert hasattr(inventory_service, 'fill_missing_snapshots')
    assert hasattr(inventory_service, 'update_inventory_snapshot')
    assert hasattr(inventory_service, 'get_restock_suggestions')
    assert hasattr(inventory_service, 'rollback_daily_sales')
    assert hasattr(inventory_service, 'get_system_status')
    assert hasattr(inventory_service, 'get_missing_report_dates')


def test_import_make_daily():
    import make_daily
    assert hasattr(make_daily, 'generate_daily_report')
    assert hasattr(make_daily, 'generate_all_missing_reports')
    assert hasattr(make_daily, 'extract_flower_from_spec')
    assert hasattr(make_daily, 'extract_meter_from_spec')
    assert hasattr(make_daily, 'load_cost_map')
    assert hasattr(make_daily, 'ensure_output_dir')


def test_import_import_order():
    import import_order
    assert hasattr(import_order, 'import_excel')
    assert hasattr(import_order, 'import_excel_from_dataframe')
    assert hasattr(import_order, 'clean_text')
    assert hasattr(import_order, 'clean_datetime')
    assert hasattr(import_order, 'clean_numeric')
    assert hasattr(import_order, 'get_latest_file')


def test_import_make_monthly():
    import make_monthly


def test_import_populate_refund_details():
    import populate_refund_details
    assert hasattr(populate_refund_details, 'sync_refund_details')
