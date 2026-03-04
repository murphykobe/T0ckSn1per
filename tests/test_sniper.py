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

from models import Task
from sniper import DayWorker


# ── Fixtures ──────────────────────────────────────────────────────────────────

def _task(**overrides) -> Task:
    defaults = dict(
        url="canlis",
        size="2",
        year="2026",
        month="March",
        days=["15"],
        earliest_time="5:00 PM",
        latest_time="9:30 PM",
    )
    defaults.update(overrides)
    return Task(**defaults)


def _make_slot_element(time_text: str) -> AsyncMock:
    """Build a mock button.Consumer-resultsListItem element."""
    span = AsyncMock()
    span.inner_text = AsyncMock(return_value=time_text)

    time_span = AsyncMock()
    time_span.query_selector = AsyncMock(return_value=span)

    slot = AsyncMock()
    slot.query_selector = AsyncMock(return_value=span)
    slot.click = AsyncMock()
    return slot


def _make_page(slots: list) -> AsyncMock:
    """Build a mock Playwright page that returns *slots* from query_selector_all."""
    page = AsyncMock()
    page.wait_for_selector = AsyncMock(return_value=None)
    page.query_selector_all = AsyncMock(return_value=slots)
    return page


def _make_worker(task=None, page=None, dry_run=False) -> DayWorker:
    task = task or _task()
    page = page or _make_page([])
    return DayWorker(
        task=task,
        day=task.days[0],
        page=page,
        found_event=asyncio.Event(),
        dry_run=dry_run,
    )


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
        task = _task()
        event = asyncio.Event()
        event.set()  # already found by another worker
        worker = DayWorker(task=task, day="15", page=page, found_event=event, dry_run=True)
        await worker.run()
        page.goto.assert_not_awaited()
