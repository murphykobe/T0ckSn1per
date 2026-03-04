"""Unit tests for recon helpers — no browser required."""

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from datetime import datetime
import pytest

from recon import _parse_time, _time_str, _build_tasks
from models import Task, RESERVATION_TIME_FORMAT


class TestParseTime:
    def test_valid_pm(self):
        dt = _parse_time("5:00 PM")
        assert dt is not None
        assert dt.hour == 17

    def test_valid_am(self):
        dt = _parse_time("11:30 AM")
        assert dt is not None
        assert dt.hour == 11
        assert dt.minute == 30

    def test_invalid_returns_none(self):
        assert _parse_time("not-a-time") is None
        assert _parse_time("25:00 PM") is None
        assert _parse_time("") is None

    def test_none_input(self):
        assert _parse_time(None) is None


class TestTimeStr:
    def test_no_leading_zero_on_hour(self):
        dt = datetime.strptime("5:00 PM", RESERVATION_TIME_FORMAT)
        result = _time_str(dt)
        assert result == "5:00 PM"
        assert not result.startswith("0")

    def test_double_digit_hour(self):
        dt = datetime.strptime("10:30 AM", RESERVATION_TIME_FORMAT)
        assert _time_str(dt) == "10:30 AM"

    def test_noon(self):
        dt = datetime.strptime("12:00 PM", RESERVATION_TIME_FORMAT)
        assert _time_str(dt) == "12:00 PM"

    def test_midnight(self):
        dt = datetime.strptime("12:00 AM", RESERVATION_TIME_FORMAT)
        result = _time_str(dt)
        assert "12:00 AM" == result


class TestBuildTasks:
    def _availability(self, **kwargs):
        """Helper to build a minimal availability dict."""
        return kwargs

    def test_basic_task_created(self):
        avail = {
            "March": {
                "year": "2026",
                "days": ["01", "15"],
                "time_slots": ["5:00 PM", "7:00 PM", "9:00 PM"],
            }
        }
        tasks = _build_tasks("canlis", "2", avail)
        assert len(tasks) == 1
        t = tasks[0]
        assert t.url == "canlis"
        assert t.size == "2"
        assert t.year == "2026"
        assert t.month == "March"
        assert t.days == ["01", "15"]

    def test_earliest_and_latest_from_slots(self):
        avail = {
            "March": {
                "year": "2026",
                "days": ["01"],
                "time_slots": ["7:00 PM", "5:00 PM", "9:30 PM"],
            }
        }
        tasks = _build_tasks("canlis", "2", avail)
        t = tasks[0]
        assert t.earliest_time == "5:00 PM"
        assert t.latest_time == "9:30 PM"

    def test_fallback_window_when_no_slots(self):
        avail = {
            "March": {
                "year": "2026",
                "days": ["01"],
                "time_slots": [],
            }
        }
        tasks = _build_tasks("canlis", "2", avail)
        t = tasks[0]
        assert t.earliest_time == "11:00 AM"
        assert t.latest_time == "11:30 PM"

    def test_fallback_when_time_slots_key_missing(self):
        avail = {
            "March": {
                "year": "2026",
                "days": ["01"],
            }
        }
        tasks = _build_tasks("canlis", "2", avail)
        t = tasks[0]
        assert t.earliest_time == "11:00 AM"
        assert t.latest_time == "11:30 PM"

    def test_skips_month_with_no_days(self):
        avail = {
            "March": {"year": "2026", "days": [], "time_slots": []},
            "April": {"year": "2026", "days": ["10"], "time_slots": ["6:00 PM"]},
        }
        tasks = _build_tasks("canlis", "2", avail)
        assert len(tasks) == 1
        assert tasks[0].month == "April"

    def test_multiple_months(self):
        avail = {
            "March": {
                "year": "2026",
                "days": ["01", "15"],
                "time_slots": ["5:00 PM", "9:00 PM"],
            },
            "April": {
                "year": "2026",
                "days": ["05"],
                "time_slots": ["6:00 PM"],
            },
        }
        tasks = _build_tasks("taneda", "4", avail)
        assert len(tasks) == 2
        months = {t.month for t in tasks}
        assert months == {"March", "April"}

    def test_invalid_time_slots_are_ignored(self):
        avail = {
            "March": {
                "year": "2026",
                "days": ["01"],
                "time_slots": ["garbage", "5:00 PM", "bad-time", "9:00 PM"],
            }
        }
        tasks = _build_tasks("canlis", "2", avail)
        t = tasks[0]
        assert t.earliest_time == "5:00 PM"
        assert t.latest_time == "9:00 PM"

    def test_empty_availability_returns_no_tasks(self):
        tasks = _build_tasks("canlis", "2", {})
        assert tasks == []
