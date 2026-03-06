"""Unit tests for recon helpers — no browser required."""

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from datetime import datetime
import pytest

from recon import _parse_time, _time_str, _build_tasks
from models import Task, Target, RESERVATION_TIME_FORMAT


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
    """
    _build_tasks(slug, size, availability) now takes:
      availability: Dict[date_str, {"time_slots": [...]}]
    and returns a list containing ONE Task with one Target per date.
    """

    def test_basic_task_created(self):
        avail = {
            "2026-03-01": {"time_slots": ["5:00 PM", "7:00 PM", "9:00 PM"]},
            "2026-03-15": {"time_slots": ["5:00 PM", "9:00 PM"]},
        }
        tasks = _build_tasks("canlis", "2", avail)
        assert len(tasks) == 1
        t = tasks[0]
        assert t.url == "canlis"
        assert t.size == "2"
        assert len(t.targets) == 2
        dates = {tgt.date for tgt in t.targets}
        assert dates == {"2026-03-01", "2026-03-15"}

    def test_earliest_and_latest_from_slots(self):
        avail = {
            "2026-03-01": {"time_slots": ["7:00 PM", "5:00 PM", "9:30 PM"]},
        }
        tasks = _build_tasks("canlis", "2", avail)
        t = tasks[0]
        tgt = t.targets[0]
        assert tgt.earliest_time == "5:00 PM"
        assert tgt.latest_time == "9:30 PM"

    def test_fallback_window_when_no_slots(self):
        avail = {
            "2026-03-01": {"time_slots": []},
        }
        tasks = _build_tasks("canlis", "2", avail)
        t = tasks[0]
        tgt = t.targets[0]
        assert tgt.earliest_time == "12:00 PM"
        assert tgt.latest_time == "11:00 PM"

    def test_fallback_when_time_slots_key_missing(self):
        avail = {
            "2026-03-01": {},
        }
        tasks = _build_tasks("canlis", "2", avail)
        t = tasks[0]
        tgt = t.targets[0]
        assert tgt.earliest_time == "12:00 PM"
        assert tgt.latest_time == "11:00 PM"

    def test_multiple_dates_produce_multiple_targets(self):
        avail = {
            "2026-03-01": {"time_slots": ["5:00 PM", "9:00 PM"]},
            "2026-04-05": {"time_slots": ["6:00 PM"]},
        }
        tasks = _build_tasks("taneda", "4", avail)
        assert len(tasks) == 1
        assert len(tasks[0].targets) == 2
        dates = {tgt.date for tgt in tasks[0].targets}
        assert dates == {"2026-03-01", "2026-04-05"}

    def test_invalid_time_slots_are_ignored(self):
        avail = {
            "2026-03-01": {"time_slots": ["garbage", "5:00 PM", "bad-time", "9:00 PM"]},
        }
        tasks = _build_tasks("canlis", "2", avail)
        tgt = tasks[0].targets[0]
        assert tgt.earliest_time == "5:00 PM"
        assert tgt.latest_time == "9:00 PM"

    def test_empty_availability_returns_no_tasks(self):
        tasks = _build_tasks("canlis", "2", {})
        assert tasks == []

    def test_target_date_preserved(self):
        avail = {
            "2026-05-20": {"time_slots": ["7:00 PM"]},
        }
        tasks = _build_tasks("alinea", "2", avail)
        assert tasks[0].targets[0].date == "2026-05-20"
