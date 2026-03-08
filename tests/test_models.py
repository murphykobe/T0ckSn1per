"""Unit tests for models.py — no browser required."""

import json
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from models import Target, Task, RESERVATION_TIME_FORMAT
from datetime import datetime


def test_target_date_parse():
    t = Target(date="2026-03-15", earliest_time="5:00 PM", latest_time="9:30 PM")
    assert t.date == "2026-03-15"
    assert t.earliest_dt() == datetime.strptime("5:00 PM", RESERVATION_TIME_FORMAT)
    assert t.latest_dt()   == datetime.strptime("9:30 PM", RESERVATION_TIME_FORMAT)


def test_target_search_url():
    t = Target(date="2026-03-15", earliest_time="5:00 PM", latest_time="9:30 PM")
    url = t.search_url("alinea", "2")
    assert "exploretock.com/alinea/search" in url
    assert "date=2026-03-15" in url
    assert "size=2" in url


def test_target_search_url_uses_midpoint_time():
    """Search URL time param should reflect target window midpoint, not hardcoded 19:00."""
    lunch = Target(date="2026-03-15", earliest_time="11:00 AM", latest_time="2:00 PM")
    url = lunch.search_url("canlis", "2")
    # Midpoint of 11:00 AM (11:00) - 2:00 PM (14:00) = 12:30
    assert "time=12%3A30" in url, f"Expected midpoint time in URL, got: {url}"

    dinner = Target(date="2026-03-15", earliest_time="5:00 PM", latest_time="9:00 PM")
    url = dinner.search_url("canlis", "2")
    # Midpoint of 5:00 PM (17:00) - 9:00 PM (21:00) = 19:00
    assert "time=19%3A00" in url, f"Expected midpoint time in URL, got: {url}"


def test_task_from_dict_roundtrip():
    data = {
        "url": "alinea",
        "size": "2",
        "targets": [
            {"date": "2026-03-15", "earliest_time": "5:00 PM", "latest_time": "9:30 PM"},
            {"date": "2026-04-01", "earliest_time": "6:00 PM", "latest_time": "10:00 PM"},
        ],
    }
    task = Task.from_dict(data)
    assert task.url == "alinea"
    assert len(task.targets) == 2
    assert task.targets[0].date == "2026-03-15"
    assert task.to_dict() == data


def test_task_empty_targets():
    task = Task(url="taneda", size="2")
    assert task.targets == []
    assert task.to_dict() == {"url": "taneda", "size": "2", "targets": []}
