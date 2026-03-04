"""Unit tests for models.py — no browser required."""

import json
import pytest
from datetime import datetime

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from models import Task, MONTH_NUM, RESERVATION_TIME_FORMAT


def _make_task(**overrides) -> Task:
    defaults = dict(
        url="canlis",
        size="2",
        year="2026",
        month="March",
        days=["01", "15", "28"],
        earliest_time="5:00 PM",
        latest_time="9:30 PM",
    )
    defaults.update(overrides)
    return Task(**defaults)


class TestSearchUrl:
    def test_basic_format(self):
        t = _make_task(url="canlis", size="2", year="2026", month="March")
        assert t.search_url() == (
            "https://www.exploretock.com/canlis/search"
            "?date=2026-03-01&size=2&time=19%3A00"
        )

    def test_month_is_case_insensitive(self):
        upper = _make_task(month="SEPTEMBER").search_url()
        lower = _make_task(month="september").search_url()
        assert upper == lower

    def test_each_month_maps_correctly(self):
        month_to_num = {
            "January": "01", "February": "02", "March": "03",
            "April": "04",   "May": "05",       "June": "06",
            "July": "07",    "August": "08",    "September": "09",
            "October": "10", "November": "11",  "December": "12",
        }
        for month, expected_num in month_to_num.items():
            url = _make_task(month=month, year="2026").search_url()
            assert f"?date=2026-{expected_num}-01" in url, f"Failed for {month}"

    def test_unknown_month_raises(self):
        with pytest.raises(KeyError):
            _make_task(month="Octember").search_url()


class TestTimeParsing:
    def test_formatted_earliest_parses(self):
        t = _make_task(earliest_time="5:00 PM")
        dt = t.formatted_earliest()
        assert isinstance(dt, datetime)
        assert dt.hour == 17
        assert dt.minute == 0

    def test_formatted_latest_parses(self):
        t = _make_task(latest_time="9:30 PM")
        dt = t.formatted_latest()
        assert dt.hour == 21
        assert dt.minute == 30

    def test_am_time_parses(self):
        t = _make_task(earliest_time="11:00 AM")
        dt = t.formatted_earliest()
        assert dt.hour == 11

    def test_noon_parses(self):
        t = _make_task(earliest_time="12:00 PM")
        dt = t.formatted_earliest()
        assert dt.hour == 12

    def test_midnight_parses(self):
        t = _make_task(latest_time="12:00 AM")
        dt = t.formatted_latest()
        assert dt.hour == 0

    def test_window_comparison(self):
        t = _make_task(earliest_time="5:00 PM", latest_time="9:30 PM")
        slot = datetime.strptime("7:00 PM", RESERVATION_TIME_FORMAT)
        assert t.formatted_earliest() <= slot <= t.formatted_latest()

    def test_slot_outside_window(self):
        t = _make_task(earliest_time="5:00 PM", latest_time="9:30 PM")
        too_late = datetime.strptime("10:00 PM", RESERVATION_TIME_FORMAT)
        assert not (t.formatted_earliest() <= too_late <= t.formatted_latest())


class TestSerialisation:
    def test_to_dict_keys(self):
        t = _make_task()
        d = t.to_dict()
        assert set(d.keys()) == {
            "url", "size", "year", "month", "days", "earliest_time", "latest_time"
        }

    def test_to_dict_values(self):
        t = _make_task(url="taneda", size="4", days=["01", "02"])
        d = t.to_dict()
        assert d["url"] == "taneda"
        assert d["size"] == "4"
        assert d["days"] == ["01", "02"]

    def test_roundtrip(self):
        original = _make_task()
        restored = Task.from_dict(original.to_dict())
        assert restored.url == original.url
        assert restored.size == original.size
        assert restored.year == original.year
        assert restored.month == original.month
        assert restored.days == original.days
        assert restored.earliest_time == original.earliest_time
        assert restored.latest_time == original.latest_time

    def test_json_roundtrip(self):
        original = _make_task()
        serialised = json.dumps(original.to_dict())
        restored = Task.from_dict(json.loads(serialised))
        assert restored == original

    def test_repr_is_valid_json(self):
        t = _make_task()
        parsed = json.loads(repr(t))
        assert parsed["url"] == "canlis"
