"""Unit tests for recon helpers — no browser required."""

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from datetime import datetime
from urllib.parse import parse_qs, urlparse
import pytest

import recon as recon_module
from recon import _parse_time, _time_str, _build_tasks, _month_starts_for_lookahead
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


def test_month_starts_for_lookahead_spans_future_months():
    starts = _month_starts_for_lookahead("2026-04-18", lookahead_days=60)
    assert starts == ["2026-04-01", "2026-05-01", "2026-06-01"]


class FixedDateTime(datetime):
    @classmethod
    def now(cls, tz=None):
        return cls(2026, 4, 18)


class FakeTimeElement:
    def __init__(self, text):
        self.text = text

    async def inner_text(self):
        return self.text


class FakeCard:
    def __init__(self, time_text):
        self.time_text = time_text

    async def query_selector(self, selector):
        if selector == "[data-testid='search-result-time']":
            return FakeTimeElement(self.time_text)
        return None


class FakeDayButton:
    def __init__(self, page, label):
        self.page = page
        self.label = label

    async def get_attribute(self, name):
        if name == "aria-label":
            return self.label
        return None

    async def click(self):
        self.page.selected_date = self.label


class FakePage:
    def __init__(self, month_data):
        self.month_data = month_data
        self.current_month = None
        self.selected_date = None

    async def goto(self, url, wait_until=None, timeout=None):
        self.current_month = parse_qs(urlparse(url).query)["date"][0]
        self.selected_date = None

    async def evaluate(self, script):
        return None

    async def wait_for_selector(self, selector, timeout=None):
        return None

    async def wait_for_timeout(self, timeout_ms):
        return None

    async def query_selector_all(self, selector):
        current_dates = self.month_data[self.current_month]
        if selector == "button[data-testid='consumer-calendar-day'][aria-disabled='false']":
            return [FakeDayButton(self, label) for label in current_dates]
        if selector == "[data-testid='search-result']":
            return [FakeCard(slot) for slot in current_dates.get(self.selected_date, [])]
        return []


class FakeBrowser:
    async def new_context(self, user_agent=None):
        return object()

    async def close(self):
        return None


class FakeChromium:
    def __init__(self, browser):
        self.browser = browser

    async def launch(self, **kwargs):
        return self.browser


class FakePlaywright:
    def __init__(self, browser):
        self.chromium = FakeChromium(browser)


class FakeAsyncPlaywright:
    def __init__(self, browser):
        self.browser = browser

    async def __aenter__(self):
        return FakePlaywright(self.browser)

    async def __aexit__(self, exc_type, exc, tb):
        return False


@pytest.mark.asyncio
async def test_scrape_restaurant_merges_months_and_filters_to_horizon(monkeypatch):
    month_data = {
        "2026-04-01": {
            "2026-04-17": ["4:30 PM"],
            "2026-04-18": ["5:15 PM"],
            "2026-04-30": ["7:45 PM"],
        },
        "2026-05-01": {
            "2026-05-01": ["5:15 PM"],
            "2026-05-31": ["7:45 PM"],
        },
        "2026-06-01": {
            "2026-06-16": ["5:15 PM"],
            "2026-06-17": ["7:45 PM"],
            "2026-06-18": ["9:30 PM"],
        },
    }
    page = FakePage(month_data)

    monkeypatch.setattr(recon_module, "datetime", FixedDateTime)
    monkeypatch.setattr(recon_module, "async_playwright", lambda: FakeAsyncPlaywright(FakeBrowser()))

    async def fake_new_page(context):
        return page

    monkeypatch.setattr(recon_module, "_new_stealth_page", fake_new_page)

    availability = await recon_module._scrape_restaurant("taneda", "1", lookahead_days=60)

    assert list(availability) == [
        "2026-04-18",
        "2026-04-30",
        "2026-05-01",
        "2026-05-31",
        "2026-06-16",
        "2026-06-17",
    ]
    assert "2026-04-17" not in availability
    assert "2026-06-18" not in availability
    assert availability["2026-05-31"]["time_slots"] == ["7:45 PM"]


@pytest.mark.asyncio
async def test_scrape_restaurant_merges_duplicate_dates_across_month_pages(monkeypatch):
    month_data = {
        "2026-04-01": {
            "2026-05-01": ["5:15 PM", "7:45 PM"],
        },
        "2026-05-01": {
            "2026-05-01": ["7:45 PM"],
        },
    }
    page = FakePage(month_data)

    monkeypatch.setattr(recon_module, "datetime", FixedDateTime)
    monkeypatch.setattr(recon_module, "async_playwright", lambda: FakeAsyncPlaywright(FakeBrowser()))

    async def fake_new_page(context):
        return page

    monkeypatch.setattr(recon_module, "_new_stealth_page", fake_new_page)

    availability = await recon_module._scrape_restaurant("taneda", "1", lookahead_days=30)

    assert availability["2026-05-01"]["time_slots"] == ["5:15 PM", "7:45 PM"]


@pytest.mark.asyncio
async def test_recon_threads_lookahead_days_into_scrape(monkeypatch):
    calls = {}

    async def fake_scrape(slug, size, lookahead_days=60):
        calls["args"] = (slug, size, lookahead_days)
        return {
            "2026-04-19": {"time_slots": ["5:15 PM", "7:45 PM"]},
        }

    monkeypatch.setattr(recon_module, "_scrape_restaurant", fake_scrape)

    tasks = await recon_module.recon("taneda", size="1", lookahead_days=45)

    assert calls["args"] == ("taneda", "1", 45)
    assert [target.date for target in tasks[0].targets] == ["2026-04-19"]


# ── _parse_next_data_json tests ──────────────────────────────────────────────

from sniper import _parse_next_data_json


class TestReconNextData:
    def test_extracts_slots_from_availabilities(self):
        next_data = {
            "props": {
                "pageProps": {
                    "availabilities": [
                        {"dateTime": "2026-03-15T17:00"},
                        {"dateTime": "2026-03-15T19:30"},
                    ]
                }
            }
        }
        result = _parse_next_data_json(next_data)
        assert result is not None
        assert "5:00 PM" in result
        assert "7:30 PM" in result

    def test_returns_none_when_no_data(self):
        assert _parse_next_data_json(None) is None

    def test_returns_none_when_no_slots(self):
        next_data = {"props": {"pageProps": {"businessName": "Canlis"}}}
        assert _parse_next_data_json(next_data) is None
