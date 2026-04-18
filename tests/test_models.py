"""Unit tests for models.py — no browser required."""

import json
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

from models import RESERVATION_TIME_FORMAT, Target, Task, expand_date_ranges
from datetime import datetime


def test_target_date_parse():
    t = Target(date="2026-03-15", earliest_time="5:00 PM", latest_time="9:30 PM")
    assert t.date == "2026-03-15"
    assert t.earliest_dt() == datetime.strptime("5:00 PM", RESERVATION_TIME_FORMAT)
    assert t.latest_dt()   == datetime.strptime("9:30 PM", RESERVATION_TIME_FORMAT)


def test_expand_date_ranges_supports_multiple_ranges():
    dates = expand_date_ranges("2026-05-07:2026-05-09,2026-05-21:2026-05-25")
    assert dates == [
        "2026-05-07",
        "2026-05-08",
        "2026-05-09",
        "2026-05-21",
        "2026-05-22",
        "2026-05-23",
        "2026-05-24",
        "2026-05-25",
    ]


def test_expand_date_ranges_returns_empty_for_blank_input():
    assert expand_date_ranges(None) == []
    assert expand_date_ranges("") == []


def test_expand_date_ranges_rejects_reversed_ranges():
    with pytest.raises(ValueError, match="2026-05-09:2026-05-07"):
        expand_date_ranges("2026-05-09:2026-05-07")


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


def test_selector_expands_dates_and_exact_times():
    from models import Selector

    selector = Selector(
        dates=["2026-06-17", "2026-06-18"],
        exact_times=["5:15 PM", "7:45 PM"],
    )

    targets = selector.expand_targets()

    assert [(t.date, t.exact_time) for t in targets] == [
        ("2026-06-17", "5:15 PM"),
        ("2026-06-17", "7:45 PM"),
        ("2026-06-18", "5:15 PM"),
        ("2026-06-18", "7:45 PM"),
    ]
    assert all(t.earliest_time == t.latest_time for t in targets)


def test_task_from_dict_accepts_selector_shape():
    task = Task.from_dict({
        "url": "taneda",
        "size": "2",
        "launch": {"release_at": "11:00", "newly_released_only": True},
        "selectors": [
            {
                "dates": ["2026-06-17"],
                "exact_times": ["5:15 PM", "7:45 PM"],
            }
        ],
    })

    assert task.launch is not None
    assert task.launch.release_at == "11:00"
    assert task.launch.newly_released_only is True
    assert len(task.expand_targets()) == 2


def test_task_from_dict_translates_legacy_targets():
    task = Task.from_dict({
        "url": "canlis",
        "size": "2",
        "targets": [
            {
                "date": "2026-03-15",
                "earliest_time": "5:00 PM",
                "latest_time": "9:30 PM",
            }
        ],
    })

    selector = task.selectors[0]
    assert selector.dates == ["2026-03-15"]
    assert selector.earliest_time == "5:00 PM"
    assert selector.latest_time == "9:30 PM"


def test_task_inline_selector_defaults_to_any_time_when_exact_times_missing():
    from models import Selector

    task = Task(
        url="taneda",
        size="2",
        selectors=[Selector(dates=["2026-06-17"])],
    )

    targets = task.expand_targets()

    assert len(targets) == 1
    assert targets[0].date == "2026-06-17"
    assert targets[0].earliest_time == "12:00 PM"
    assert targets[0].latest_time == "11:00 PM"


def test_task_filter_dates_preserves_exact_times():
    from models import Selector

    task = Task(
        url="taneda",
        size="2",
        selectors=[
            Selector(
                dates=["2026-06-17", "2026-06-18"],
                exact_times=["5:15 PM", "7:45 PM"],
            )
        ],
    )

    filtered = task.filter_dates(["2026-06-18"])

    assert [t.date for t in filtered.expand_targets()] == ["2026-06-18", "2026-06-18"]
    assert [t.exact_time for t in filtered.expand_targets()] == ["5:15 PM", "7:45 PM"]


def test_task_filter_dates_expands_empty_date_preferences_to_eligible_dates():
    from models import Selector

    task = Task(
        url="taneda",
        size="1",
        selectors=[Selector(dates=[])],
    )

    filtered = task.filter_dates(["2026-05-27", "2026-05-28"])

    assert [t.date for t in filtered.expand_targets()] == ["2026-05-27", "2026-05-28"]
    assert all(t.earliest_time == "12:00 PM" for t in filtered.expand_targets())
