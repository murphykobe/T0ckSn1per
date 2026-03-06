"""
Unit tests for sniper.DayWorker — no browser required.

All Playwright Page interactions are replaced with AsyncMock objects so
these tests run instantly without launching Chrome.
"""

import asyncio
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from models import Task, Target
from sniper import DayWorker


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def task():
    return Task(url="alinea", size="2", targets=[
        Target(date="2026-03-15", earliest_time="5:00 PM", latest_time="9:30 PM"),
    ])

@pytest.fixture
def target(task):
    return task.targets[0]


def _make_slot_element(time_text: str) -> AsyncMock:
    """
    Build a mock search-result card element.

    The card has two child elements accessible via query_selector:
      - [data-testid='search-result-time']       → time_el (returns time_text)
      - button[data-testid='booking-card-button'] → book_btn (click tracked on slot.click)

    slot.click is used as the book button's click so tests can assert on it.
    """
    time_el = AsyncMock()
    time_el.inner_text = AsyncMock(return_value=time_text)

    book_btn = AsyncMock()

    slot = AsyncMock()

    async def _query_selector(selector):
        if "search-result-time" in selector:
            return time_el
        if "booking-card-button" in selector:
            return book_btn
        return None

    slot.query_selector = _query_selector
    # Expose book_btn.click as slot.click so test assertions work intuitively
    slot.click = book_btn.click
    return slot


def _make_page(slots: list) -> AsyncMock:
    """Build a mock Playwright page that returns *slots* from query_selector_all."""
    page = AsyncMock()
    page.wait_for_selector = AsyncMock(return_value=None)
    page.query_selector_all = AsyncMock(return_value=slots)
    return page


def _make_worker(task=None, target=None, page=None, dry_run=False) -> DayWorker:
    if task is None:
        task = Task(url="canlis", size="2", targets=[
            Target(date="2026-03-15", earliest_time="5:00 PM", latest_time="9:30 PM"),
        ])
    if target is None:
        target = task.targets[0]
    page = page or _make_page([])
    return DayWorker(
        task=task,
        target=target,
        page=page,
        found_event=asyncio.Event(),
        dry_run=dry_run,
    )


# ── New Target-based constructor tests ────────────────────────────────────────

@pytest.mark.asyncio
async def test_day_worker_accepts_target(task, target):
    page = MagicMock()
    event = asyncio.Event()
    worker = DayWorker(task=task, target=target, page=page, found_event=event)
    assert worker.target.date == "2026-03-15"
    assert worker.checkout_url is None


@pytest.mark.asyncio
async def test_day_worker_try_day_uses_target_date(task, target):
    page = AsyncMock()
    page.query_selector = AsyncMock(return_value=None)
    event = asyncio.Event()
    worker = DayWorker(task=task, target=target, page=page, found_event=event)
    result = await worker._try_day()
    assert result is False
    call_args = page.query_selector.call_args[0][0]
    assert "2026-03-15" in call_args
    assert "consumer-calendar-day" in call_args


# ── _try_time tests ───────────────────────────────────────────────────────────

class TestTryTime:
    @pytest.mark.asyncio
    async def test_slot_in_window_returns_true(self):
        slot = _make_slot_element("7:00 PM")
        page = _make_page([slot])
        worker = _make_worker(page=page)
        assert await worker._try_time() is True

    @pytest.mark.asyncio
    async def test_slot_at_earliest_boundary_returns_true(self):
        slot = _make_slot_element("5:00 PM")
        page = _make_page([slot])
        worker = _make_worker(page=page)
        assert await worker._try_time() is True

    @pytest.mark.asyncio
    async def test_slot_at_latest_boundary_returns_true(self):
        slot = _make_slot_element("9:30 PM")
        page = _make_page([slot])
        worker = _make_worker(page=page)
        assert await worker._try_time() is True

    @pytest.mark.asyncio
    async def test_slot_before_window_returns_false(self):
        slot = _make_slot_element("4:30 PM")
        page = _make_page([slot])
        worker = _make_worker(page=page)
        assert await worker._try_time() is False

    @pytest.mark.asyncio
    async def test_slot_after_window_returns_false(self):
        slot = _make_slot_element("10:00 PM")
        page = _make_page([slot])
        worker = _make_worker(page=page)
        assert await worker._try_time() is False

    @pytest.mark.asyncio
    async def test_no_slots_returns_false(self):
        page = _make_page([])
        worker = _make_worker(page=page)
        assert await worker._try_time() is False

    @pytest.mark.asyncio
    async def test_picks_first_slot_in_window(self):
        """When multiple slots are in-window, only the first is clicked."""
        slot_a = _make_slot_element("5:30 PM")
        slot_b = _make_slot_element("6:00 PM")
        page = _make_page([slot_a, slot_b])
        worker = _make_worker(page=page)
        result = await worker._try_time()
        assert result is True
        slot_a.click.assert_awaited_once()
        slot_b.click.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_skips_out_of_window_then_picks_in_window(self):
        """First slot is too early; second is in-window and should be clicked."""
        early = _make_slot_element("3:00 PM")
        good  = _make_slot_element("6:00 PM")
        page  = _make_page([early, good])
        worker = _make_worker(page=page)
        assert await worker._try_time() is True
        early.click.assert_not_awaited()
        good.click.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_dry_run_does_not_click(self):
        slot = _make_slot_element("7:00 PM")
        page = _make_page([slot])
        worker = _make_worker(page=page, dry_run=True)
        result = await worker._try_time()
        assert result is True
        slot.click.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_invalid_time_text_skipped(self):
        """Slots with unparseable time text should be skipped gracefully."""
        bad  = _make_slot_element("not-a-time")
        good = _make_slot_element("7:00 PM")
        page = _make_page([bad, good])
        worker = _make_worker(page=page)
        assert await worker._try_time() is True
        good.click.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_timeout_returns_false(self):
        """If wait_for_selector times out, _try_time returns False."""
        from playwright.async_api import TimeoutError as PWTimeout
        page = AsyncMock()
        page.wait_for_selector = AsyncMock(side_effect=PWTimeout("timed out"))
        worker = _make_worker(page=page)
        assert await worker._try_time() is False


# ── found_event propagation ───────────────────────────────────────────────────

class TestFoundEvent:
    @pytest.mark.asyncio
    async def test_run_stops_when_event_set_before_first_poll(self):
        """If found_event is already set, run() exits without polling."""
        page = AsyncMock()
        page.goto = AsyncMock()
        task = Task(url="canlis", size="2", targets=[
            Target(date="2026-03-15", earliest_time="5:00 PM", latest_time="9:30 PM"),
        ])
        target = task.targets[0]
        event = asyncio.Event()
        event.set()  # already found by another worker
        worker = DayWorker(task=task, target=target, page=page, found_event=event, dry_run=True)
        await worker.run()
        page.goto.assert_not_awaited()


# ── Cookie parsing ────────────────────────────────────────────────────────────

def test_parse_netscape_cookies():
    from sniper import _parse_netscape_cookies
    lines = [
        "# Netscape HTTP Cookie File\n",
        ".exploretock.com\tTRUE\t/\tTRUE\t0\t_tock_session\tabc123\n",
        "# comment line\n",
        ".exploretock.com\tTRUE\t/\tFALSE\t0\ttock_user\txyz\n",
        "bad line no tabs\n",
    ]
    cookies = _parse_netscape_cookies(lines)
    assert len(cookies) == 2
    assert cookies[0]["name"] == "_tock_session"
    assert cookies[0]["value"] == "abc123"
    assert cookies[0]["secure"] is True
    assert cookies[1]["name"] == "tock_user"
    assert cookies[1]["secure"] is False


# ── interval / max-duration / release-at tests ────────────────────────────────

@pytest.mark.asyncio
async def test_day_worker_uses_interval(task, target):
    """DayWorker stores interval for use in polling loop."""
    page = MagicMock()
    event = asyncio.Event()
    worker = DayWorker(task=task, target=target, page=page, found_event=event, interval=15.0)
    assert worker.interval == 15.0

@pytest.mark.asyncio
async def test_wait_for_release_past_time():
    """_wait_for_release returns immediately if time already passed."""
    from sniper import _wait_for_release
    # Use a time in the past (00:00 is always past during the day)
    await _wait_for_release("00:00")  # Should return immediately, not sleep
