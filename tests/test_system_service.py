"""Tests for :mod:`system_service`.

All DB interactions are routed through the mocked ``mysql_conn.engine``.
"""
from unittest.mock import MagicMock, patch, call
from datetime import date
import pytest

from system_service import (
    get_system_start_date,
    set_system_start_date,
    get_date_range_for_display,
)


class TestGetSystemStartDate:

    def test_returns_configured_date(self, mock_conn):
        """When system_config has a value, return it as a date."""
        mock_conn.execute.return_value.fetchone.return_value = ('2026-07-01',)
        result = get_system_start_date()
        assert result == date(2026, 7, 1)

    def test_returns_default_when_no_config(self, mock_conn):
        """When no row exists, fall back to 2026-07-01."""
        mock_conn.execute.return_value.fetchone.return_value = None
        result = get_system_start_date()
        assert result == date(2026, 7, 1)


class TestSetSystemStartDate:

    def test_set_date(self, mock_conn):
        result = set_system_start_date(date(2026, 8, 1))
        assert result is True
        mock_conn.execute.assert_called_once()

    def test_set_date_back_to_default(self, mock_conn):
        result = set_system_start_date(date(2026, 7, 1))
        assert result is True


class TestGetDateRangeForDisplay:

    @patch('pandas.read_sql')
    def test_with_data(self, mock_read_sql, mock_conn):
        """When all tables have data, return the ranges."""
        mock_conn.execute.side_effect = [
            MagicMock(fetchone=lambda: ('2026-07-01',)),  # get_system_start_date
            MagicMock(scalar=lambda: '2026-07-01'),  # earliest_order
            MagicMock(scalar=lambda: '2026-07-15'),  # latest_order
            MagicMock(scalar=lambda: '2026-07-15'),  # latest_report
            MagicMock(scalar=lambda: '2026-07-15'),  # latest_snapshot
        ]

        result = get_date_range_for_display()
        assert result['earliest_order'] == '2026-07-01'
        assert result['latest_order'] == '2026-07-15'
        assert result['latest_report'] == '2026-07-15'
        assert result['latest_snapshot'] == '2026-07-15'
        assert result['start_date'] is not None

    @patch('pandas.read_sql')
    def test_without_data(self, mock_read_sql, mock_conn):
        """When no data exists, return None for all ranges."""
        mock_conn.execute.return_value.fetchone.return_value = None
        mock_conn.execute.return_value.scalar.return_value = None
        result = get_date_range_for_display()
        assert result['earliest_order'] is None
        assert result['latest_order'] is None
