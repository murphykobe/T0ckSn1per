"""
Unit tests for sniper.DayWorker — no browser required.

All Playwright Page interactions are replaced with AsyncMock objects so
these tests run instantly without launching Chrome.
"""

import asyncio
import time
import warnings
import sys, os
from datetime import datetime
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from models import LaunchConfig, Selector, Task, Target
from sniper import DayWorker


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def task():
    return Task(url="alinea", size="2", selectors=[
        Selector(
            dates=["2026-03-15"],
            earliest_time="5:00 PM",
            latest_time="9:30 PM",
        ),
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
        task = Task(url="canlis", size="2", selectors=[
            Selector(
                dates=["2026-03-15"],
                earliest_time="5:00 PM",
                latest_time="9:30 PM",
            ),
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


@pytest.mark.asyncio
async def test_day_worker_try_day_waits_briefly_for_target_button(task, target):
    page = AsyncMock()
    button = AsyncMock()
    page.query_selector = AsyncMock(side_effect=[None, button])
    event = asyncio.Event()
    worker = DayWorker(task=task, target=target, page=page, found_event=event)
    worker._try_time = AsyncMock(return_value=True)

    with patch("sniper.asyncio.sleep", AsyncMock()) as sleep_mock:
        result = await worker._try_day()

    assert result is True
    assert page.query_selector.await_count == 2
    button.click.assert_awaited_once()
    worker._try_time.assert_awaited_once()
    sleep_mock.assert_awaited_once()


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

    @pytest.mark.asyncio
    async def test_rejects_failed_cart_add(self):
        """_try_time returns False when checkout URL doesn't confirm cart."""
        from playwright.async_api import TimeoutError as PWTimeout
        slot = _make_slot_element("6:00 PM")
        page = _make_page([slot])
        # After clicking, URL stays on search page (cart add failed)
        page.url = "https://www.exploretock.com/alinea/search?date=2026-03-15"
        page.wait_for_url = AsyncMock(side_effect=PWTimeout("timeout"))
        # No holding-time element, no "Complete your reservation" text
        page.query_selector = AsyncMock(return_value=None)

        worker = _make_worker(page=page)
        result = await worker._try_time()
        assert result is False, "Should reject when cart add is not confirmed"
        assert worker.checkout_url is None


# ── found_event propagation ───────────────────────────────────────────────────

class TestFoundEvent:
    @pytest.mark.asyncio
    async def test_run_stops_when_event_set_before_first_poll(self):
        """If found_event is already set, run() exits without polling."""
        page = AsyncMock()
        page.goto = AsyncMock()
        task = Task(url="canlis", size="2", selectors=[
            Selector(
                dates=["2026-03-15"],
                earliest_time="5:00 PM",
                latest_time="9:30 PM",
            ),
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


@pytest.mark.asyncio
async def test_snipe_all_empty_returns_empty_list():
    from sniper import snipe_all
    results = await snipe_all([])
    assert results == []


# ── prompt_login flag ─────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_day_worker_stores_prompt_login(task, target):
    """DayWorker stores prompt_login flag."""
    page = MagicMock()
    event = asyncio.Event()
    worker = DayWorker(task=task, target=target, page=page, found_event=event, prompt_login=True)
    assert worker.prompt_login is True

@pytest.mark.asyncio
async def test_day_worker_prompt_login_default_false(task, target):
    """DayWorker prompt_login defaults to False."""
    page = MagicMock()
    event = asyncio.Event()
    worker = DayWorker(task=task, target=target, page=page, found_event=event)
    assert worker.prompt_login is False


@pytest.mark.asyncio
async def test_day_worker_polls_immediately_first_iteration(task, target):
    """DayWorker should not sleep before its first poll attempt."""
    page = AsyncMock()
    page.goto = AsyncMock()
    page.wait_for_selector = AsyncMock(side_effect=Exception("stop"))
    event = asyncio.Event()
    worker = DayWorker(task=task, target=target, page=page, found_event=event, interval=30.0)

    start = time.monotonic()
    # Run worker briefly — it should attempt _poll almost immediately, not after 30s
    try:
        await asyncio.wait_for(worker.run(), timeout=2.0)
    except asyncio.TimeoutError:
        pass
    elapsed = time.monotonic() - start

    # Worker should have called page.goto (attempted a poll) within 2 seconds
    assert page.goto.called, "Worker never attempted to poll"
    assert elapsed < 3.0, f"Worker took {elapsed:.1f}s — should poll immediately, not sleep first"


@pytest.mark.asyncio
async def test_exact_time_clicks_only_exact_match():
    early = _make_slot_element("5:00 PM")
    exact = _make_slot_element("5:15 PM")
    later = _make_slot_element("7:45 PM")
    page = _make_page([early, exact, later])
    target = Target(
        date="2026-06-17",
        earliest_time="5:15 PM",
        latest_time="5:15 PM",
        exact_time="5:15 PM",
    )
    worker = _make_worker(target=target, page=page)

    result = await worker._try_time()

    assert result is True
    early.click.assert_not_awaited()
    exact.click.assert_awaited_once()
    later.click.assert_not_awaited()


@pytest.mark.asyncio
async def test_exact_time_ignores_non_exact_slots_inside_window():
    inside_window = _make_slot_element("6:00 PM")
    exact = _make_slot_element("7:45 PM")
    page = _make_page([inside_window, exact])
    target = Target(
        date="2026-06-17",
        earliest_time="5:15 PM",
        latest_time="9:30 PM",
        exact_time="7:45 PM",
    )
    worker = _make_worker(target=target, page=page)

    result = await worker._try_time()

    assert result is True
    inside_window.click.assert_not_awaited()
    exact.click.assert_awaited_once()


# ── _extract_next_data tests ─────────────────────────────────────────────────

import json

class TestExtractNextData:
    @pytest.mark.asyncio
    async def test_returns_slots_from_pageprops(self):
        """Extracts times from pageProps.availabilities path; ISO datetimes convert correctly."""
        next_data = {
            "props": {
                "pageProps": {
                    "availabilities": [
                        {"dateTime": "2026-03-15T17:00"},
                        {"dateTime": "2026-03-15T19:30"},
                        {"dateTime": "2026-03-15T21:00"},
                    ]
                }
            }
        }
        page = AsyncMock()
        page.evaluate = AsyncMock(return_value=next_data)
        worker = _make_worker(page=page)
        result = await worker._extract_next_data()
        assert result is not None
        assert "5:00 PM" in result
        assert "7:30 PM" in result
        assert "9:00 PM" in result

    @pytest.mark.asyncio
    async def test_returns_none_when_no_next_data_element(self):
        """page.evaluate returns None → returns None."""
        page = AsyncMock()
        page.evaluate = AsyncMock(return_value=None)
        worker = _make_worker(page=page)
        result = await worker._extract_next_data()
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_when_no_slots_found(self):
        """__NEXT_DATA__ exists but no availability keys → returns None."""
        next_data = {
            "props": {
                "pageProps": {
                    "restaurantName": "Alinea",
                    "someOtherKey": 123,
                }
            }
        }
        page = AsyncMock()
        page.evaluate = AsyncMock(return_value=next_data)
        worker = _make_worker(page=page)
        result = await worker._extract_next_data()
        assert result is None

    @pytest.mark.asyncio
    async def test_handles_iso_datetime_format(self):
        """ISO datetime 2026-03-15T14:00 → 2:00 PM."""
        next_data = {
            "props": {
                "pageProps": {
                    "availability": [
                        {"startTime": "2026-03-15T14:00"},
                    ]
                }
            }
        }
        page = AsyncMock()
        page.evaluate = AsyncMock(return_value=next_data)
        worker = _make_worker(page=page)
        result = await worker._extract_next_data()
        assert result is not None
        assert "2:00 PM" in result

    @pytest.mark.asyncio
    async def test_handles_nested_arrays(self):
        """Finds slots inside nested dict values that are arrays."""
        next_data = {
            "props": {
                "pageProps": {
                    "searchResults": {
                        "results": [
                            {"time": "17:00"},
                            {"nested": {"time": "19:00"}},
                        ]
                    }
                }
            }
        }
        page = AsyncMock()
        page.evaluate = AsyncMock(return_value=next_data)
        worker = _make_worker(page=page)
        result = await worker._extract_next_data()
        assert result is not None
        assert "5:00 PM" in result
        assert "7:00 PM" in result

    @pytest.mark.asyncio
    async def test_handles_evaluate_exception(self):
        """page.evaluate throws → returns None gracefully."""
        page = AsyncMock()
        page.evaluate = AsyncMock(side_effect=Exception("browser crashed"))
        worker = _make_worker(page=page)
        result = await worker._extract_next_data()
        assert result is None

    @pytest.mark.asyncio
    async def test_dehydrated_state_path(self):
        """Extracts from dehydratedState.queries[0].state.data."""
        next_data = {
            "props": {
                "pageProps": {
                    "dehydratedState": {
                        "queries": [
                            {
                                "state": {
                                    "data": [
                                        {"startTime": "2026-03-15T18:00"},
                                        {"startTime": "2026-03-15T20:30"},
                                    ]
                                }
                            }
                        ]
                    }
                }
            }
        }
        page = AsyncMock()
        page.evaluate = AsyncMock(return_value=next_data)
        worker = _make_worker(page=page)
        result = await worker._extract_next_data()
        assert result is not None
        assert "6:00 PM" in result
        assert "8:30 PM" in result


def test_parse_next_data_json_returns_none_for_non_dict():
    from sniper import _parse_next_data_json
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        result = _parse_next_data_json(AsyncMock())

    assert result is None
    assert caught == []


def test_newly_released_dates_applies_delta_and_filter():
    from sniper import _newly_released_dates

    result = _newly_released_dates(
        before={"2026-06-10", "2026-06-11"},
        after={"2026-06-10", "2026-06-11", "2026-06-17", "2026-06-18"},
        requested_dates=["2026-06-17", "2026-06-19"],
    )

    assert result == ["2026-06-17"]


class FixedDateTime(datetime):
    @classmethod
    def now(cls, tz=None):
        return cls(2026, 4, 18)


def test_default_launch_window_dates_uses_next_30_calendar_days(monkeypatch):
    from sniper import _default_launch_window_dates

    monkeypatch.setattr("sniper.datetime", FixedDateTime)

    assert _default_launch_window_dates(3) == [
        "2026-04-18",
        "2026-04-19",
        "2026-04-20",
    ]


@pytest.mark.asyncio
async def test_launch_mode_does_not_exit_after_first_empty_new_release_diff():
    from sniper import _monitor_newly_released_dates

    task = Task(url="taneda", size="1", selectors=[
        Selector(dates=["2026-05-21"]),
    ])
    page = AsyncMock()

    with patch(
        "sniper._capture_available_dates",
        AsyncMock(side_effect=[
            {"2026-05-01"},
            {"2026-05-01", "2026-05-21"},
        ]),
    ) as capture_mock:
        with patch("sniper.asyncio.sleep", AsyncMock()) as sleep_mock:
            result = await _monitor_newly_released_dates(
                page,
                task,
                requested_dates=["2026-05-21"],
                interval=5.0,
                deadline=asyncio.get_running_loop().time() + 60,
                baseline_dates={"2026-05-01"},
            )

    assert result == ["2026-05-21"]
    assert capture_mock.await_count == 2
    sleep_mock.assert_awaited_once()


@pytest.mark.asyncio
async def test_monitor_newly_released_dates_returns_empty_after_deadline():
    from sniper import _monitor_newly_released_dates

    task = Task(url="taneda", size="1", selectors=[
        Selector(dates=["2026-05-21"]),
    ])
    page = AsyncMock()

    with patch(
        "sniper._capture_available_dates",
        AsyncMock(return_value={"2026-05-01"}),
    ):
        with patch("sniper.asyncio.sleep", AsyncMock()) as sleep_mock:
            result = await _monitor_newly_released_dates(
                page,
                task,
                requested_dates=["2026-05-21"],
                interval=5.0,
                deadline=asyncio.get_running_loop().time(),
                baseline_dates={"2026-05-01"},
            )

    assert result == []
    sleep_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_open_browser_context_uses_cdp_when_url_provided():
    from sniper import _open_browser_context

    playwright = AsyncMock()
    remote_browser = AsyncMock()
    existing_context = AsyncMock()
    remote_browser.contexts = [existing_context]
    playwright.chromium.connect_over_cdp = AsyncMock(return_value=remote_browser)

    browser, context, owns_browser, owns_context = await _open_browser_context(
        playwright,
        cdp_url="http://127.0.0.1:9222",
    )

    playwright.chromium.connect_over_cdp.assert_awaited_once_with("http://127.0.0.1:9222")
    assert browser is remote_browser
    assert context is existing_context
    assert owns_browser is False
    assert owns_context is False


@pytest.mark.asyncio
async def test_open_browser_context_launches_browser_when_cdp_missing():
    from sniper import _open_browser_context

    playwright = AsyncMock()
    browser = AsyncMock()
    context = AsyncMock()
    playwright.chromium.launch = AsyncMock(return_value=browser)
    browser.new_context = AsyncMock(return_value=context)

    opened_browser, opened_context, owns_browser, owns_context = await _open_browser_context(
        playwright,
        cdp_url=None,
    )

    playwright.chromium.launch.assert_awaited()
    browser.new_context.assert_awaited()
    assert opened_browser is browser
    assert opened_context is context
    assert owns_browser is True
    assert owns_context is True


class _FakePlaywrightContextManager:
    def __init__(self, playwright):
        self._playwright = playwright

    async def __aenter__(self):
        return self._playwright

    async def __aexit__(self, exc_type, exc, tb):
        return False


@pytest.mark.asyncio
async def test_snipe_task_cancels_workers_before_browser_close():
    from sniper import snipe_task

    order = []
    browser = AsyncMock()
    context = AsyncMock()
    page = AsyncMock()
    context.new_page = AsyncMock(return_value=page)

    async def _close_browser():
        order.append("browser.close")

    browser.close = AsyncMock(side_effect=_close_browser)

    class FakeWorker:
        def __init__(self, task, target, page, found_event, dry_run=False, interval=30.0, prompt_login=False):
            self.task = task
            self.target = target
            self.page = page
            self.checkout_url = None

        async def run(self):
            try:
                await asyncio.Future()
            except asyncio.CancelledError:
                order.append("worker.cancelled")
                raise

    with patch("sniper.async_playwright", return_value=_FakePlaywrightContextManager(AsyncMock())):
        with patch("sniper._open_browser_context", AsyncMock(return_value=(browser, context, True, True))):
            with patch("sniper._apply_stealth", AsyncMock()):
                with patch("sniper.DayWorker", FakeWorker):
                    task = Task(url="alinea", size="2", selectors=[
                        Selector(
                            dates=["2026-03-15"],
                            earliest_time="5:00 PM",
                            latest_time="9:30 PM",
                        ),
                    ])

                    result = await snipe_task(task, dry_run=True, max_duration=0.001)

    assert result is None
    assert order == ["worker.cancelled", "browser.close"]


@pytest.mark.asyncio
async def test_snipe_task_does_not_close_attached_cdp_browser():
    from sniper import snipe_task

    browser = AsyncMock()
    context = AsyncMock()
    page = AsyncMock()
    context.new_page = AsyncMock(return_value=page)

    class FakeWorker:
        def __init__(self, task, target, page, found_event, dry_run=False, interval=30.0, prompt_login=False):
            self.task = task
            self.target = target
            self.page = page
            self.checkout_url = None

        async def run(self):
            return None

    with patch("sniper.async_playwright", return_value=_FakePlaywrightContextManager(AsyncMock())):
        with patch("sniper._open_browser_context", AsyncMock(return_value=(browser, context, False, False))):
            with patch("sniper._apply_stealth", AsyncMock()):
                with patch("sniper.DayWorker", FakeWorker):
                    task = Task(url="alinea", size="2", selectors=[
                        Selector(
                            dates=["2026-03-15"],
                            earliest_time="5:00 PM",
                            latest_time="9:30 PM",
                        ),
                    ])

                    result = await snipe_task(task, dry_run=True, max_duration=0.001)

    assert result is None
    browser.close.assert_not_awaited()
    context.close.assert_not_awaited()
    page.close.assert_not_awaited()


@pytest.mark.asyncio
async def test_snipe_task_cleans_up_stragglers_after_found_event_before_browser_close():
    from sniper import snipe_task

    order = []
    browser = AsyncMock()
    context = AsyncMock()
    winner_page = AsyncMock()
    straggler_page = AsyncMock()
    context.new_page = AsyncMock(side_effect=[winner_page, straggler_page])

    async def _close_browser():
        order.append("browser.close")

    browser.close = AsyncMock(side_effect=_close_browser)

    class FakeWorker:
        instances = []

        def __init__(self, task, target, page, found_event, dry_run=False, interval=30.0, prompt_login=False):
            self.task = task
            self.target = target
            self.page = page
            self.found_event = found_event
            self.checkout_url = None
            self.matched_time = None
            FakeWorker.instances.append(self)

        async def run(self):
            if self.page is winner_page:
                self.checkout_url = "(dry-run)"
                self.found_event.set()
                order.append("winner.done")
                return None

            try:
                await asyncio.Future()
            except asyncio.CancelledError:
                order.append("straggler.cancelled")
                raise

    with patch("sniper.async_playwright", return_value=_FakePlaywrightContextManager(AsyncMock())):
        with patch("sniper._open_browser_context", AsyncMock(return_value=(browser, context, True, True))):
            with patch("sniper._apply_stealth", AsyncMock()):
                with patch("sniper.DayWorker", FakeWorker):
                    task = Task(url="alinea", size="2", selectors=[
                        Selector(
                            dates=["2026-03-15", "2026-03-16"],
                            earliest_time="5:00 PM",
                            latest_time="9:30 PM",
                        ),
                    ])

                    result = await snipe_task(task, dry_run=True)

    assert result == {
        "restaurant": "alinea",
        "date": "2026-03-15",
        "time": "",
        "checkout_url": "(dry-run)",
    }
    assert order == ["winner.done", "straggler.cancelled", "browser.close"]


@pytest.mark.asyncio
async def test_snipe_task_monitoring_deadline_cancels_workers_before_browser_close():
    from sniper import snipe_task

    order = []
    browser = AsyncMock()
    context = AsyncMock()
    page = AsyncMock()
    context.new_page = AsyncMock(return_value=page)

    async def _close_browser():
        order.append("browser.close")

    browser.close = AsyncMock(side_effect=_close_browser)

    class FakeWorker:
        def __init__(self, task, target, page, found_event, dry_run=False, interval=30.0, prompt_login=False):
            self.task = task
            self.target = target
            self.page = page
            self.checkout_url = None

        async def run(self):
            try:
                await asyncio.Future()
            except asyncio.CancelledError:
                order.append("worker.cancelled")
                raise

    with patch("sniper.async_playwright", return_value=_FakePlaywrightContextManager(AsyncMock())):
        with patch("sniper._open_browser_context", AsyncMock(return_value=(browser, context, True, True))):
            with patch("sniper._apply_stealth", AsyncMock()):
                with patch("sniper.DayWorker", FakeWorker):
                    task = Task(url="alinea", size="2", selectors=[
                        Selector(
                            dates=["2026-03-15"],
                            earliest_time="5:00 PM",
                            latest_time="9:30 PM",
                        ),
                    ])

                    result = await snipe_task(
                        task,
                        dry_run=True,
                        monitor=True,
                        monitor_duration=0.001,
                    )

    assert result is None
    assert order == ["worker.cancelled", "browser.close"]


@pytest.mark.asyncio
async def test_snipe_task_launch_monitoring_retries_until_new_dates_found():
    from sniper import snipe_task

    browser = AsyncMock()
    context = AsyncMock()
    scout = AsyncMock()
    worker_page = AsyncMock()
    context.new_page = AsyncMock(side_effect=[scout, worker_page])

    class FakeWorker:
        created_targets = []

        def __init__(self, task, target, page, found_event, dry_run=False, interval=30.0, prompt_login=False):
            self.task = task
            self.target = target
            self.page = page
            self.checkout_url = "(dry-run)"
            self.matched_time = target.exact_time or target.earliest_time
            FakeWorker.created_targets.append(target.date)

        async def run(self):
            return None

    task = Task(
        url="taneda",
        size="1",
        selectors=[Selector(dates=["2026-05-21"], exact_times=["5:15 PM"])],
        launch=LaunchConfig(release_at="11:00", newly_released_only=True),
    )
    FakeWorker.created_targets = []

    with patch("sniper.async_playwright", return_value=_FakePlaywrightContextManager(AsyncMock())):
        with patch("sniper._open_browser_context", AsyncMock(return_value=(browser, context, True, True))):
            with patch("sniper._apply_stealth", AsyncMock()):
                with patch("sniper._wait_for_release", AsyncMock()):
                    with patch("sniper.DayWorker", FakeWorker):
                        with patch(
                            "sniper._capture_available_dates",
                            AsyncMock(side_effect=[
                                {"2026-05-01"},
                                {"2026-05-01"},
                                {"2026-05-01", "2026-05-21"},
                            ]),
                        ):
                            with patch("sniper.asyncio.sleep", AsyncMock()):
                                result = await snipe_task(task, dry_run=True, interval=5.0)

    assert result == {
        "restaurant": "taneda",
        "date": "2026-05-21",
        "time": "5:15 PM",
        "checkout_url": "(dry-run)",
    }
    assert FakeWorker.created_targets == ["2026-05-21"]


@pytest.mark.asyncio
async def test_snipe_all_forwards_monitoring_kwargs():
    from sniper import snipe_all

    task = Task(url="alinea", size="2", selectors=[
        Selector(
            dates=["2026-03-15"],
            earliest_time="5:00 PM",
            latest_time="9:30 PM",
        ),
    ])

    with patch(
        "sniper.snipe_task",
        AsyncMock(return_value={
            "restaurant": "alinea",
            "date": "2026-03-15",
            "time": "7:00 PM",
            "checkout_url": "(dry-run)",
        }),
    ) as snipe_task_mock:
        results = await snipe_all(
            [task],
            dry_run=True,
            monitor=True,
            monitor_duration=3.5,
        )

    snipe_task_mock.assert_awaited_once_with(
        task,
        dry_run=True,
        monitor=True,
        monitor_duration=3.5,
    )
    assert results == [{
        "status": "success",
        "restaurant": "alinea",
        "date": "2026-03-15",
        "time": "7:00 PM",
        "checkout_url": "(dry-run)",
    }]


# ── _poll() with __NEXT_DATA__ pre-filter tests ─────────────────────────────

class TestPollWithNextData:
    @pytest.mark.asyncio
    async def test_poll_skips_dom_when_next_data_has_no_matching_slots(self):
        """__NEXT_DATA__ returns slots but none in window -> _try_day NOT called, returns False."""
        page = AsyncMock()
        page.goto = AsyncMock()
        # __NEXT_DATA__ returns slots outside the 5:00 PM - 9:30 PM window
        page.evaluate = AsyncMock(return_value={
            "props": {
                "pageProps": {
                    "availabilities": [
                        {"dateTime": "2026-03-15T11:00"},
                        {"dateTime": "2026-03-15T14:00"},
                    ]
                }
            }
        })
        page.wait_for_selector = AsyncMock()
        page.wait_for_timeout = AsyncMock()

        worker = _make_worker(page=page)
        worker._try_day = AsyncMock(return_value=False)

        result = await worker._poll()

        assert result is False
        worker._try_day.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_poll_proceeds_to_dom_when_next_data_has_matching_slot(self):
        """__NEXT_DATA__ finds a slot in window -> _try_day IS called."""
        page = AsyncMock()
        page.goto = AsyncMock()
        # __NEXT_DATA__ returns a slot inside the 5:00 PM - 9:30 PM window
        page.evaluate = AsyncMock(return_value={
            "props": {
                "pageProps": {
                    "availabilities": [
                        {"dateTime": "2026-03-15T18:00"},  # 6:00 PM — in window
                    ]
                }
            }
        })
        page.wait_for_selector = AsyncMock()
        page.wait_for_timeout = AsyncMock()

        worker = _make_worker(page=page)
        worker._try_day = AsyncMock(return_value=True)

        result = await worker._poll()

        assert result is True
        worker._try_day.assert_awaited_once()


@pytest.mark.asyncio
async def test_wait_for_selector_until_stop_exits_when_found_event_set():
    from playwright.async_api import TimeoutError as PWTimeout

    page = AsyncMock()
    worker = _make_worker(page=page)

    async def _wait_side_effect(*args, **kwargs):
        worker.found_event.set()
        raise PWTimeout("not yet")

    page.wait_for_selector = AsyncMock(side_effect=_wait_side_effect)

    result = await worker._wait_for_selector_until_stop(
        "div.ConsumerCalendar-month",
        timeout_ms=1_000,
        step_ms=10,
    )

    assert result is False


@pytest.mark.asyncio
async def test_poll_falls_back_to_dom_when_next_data_unavailable():
    """__NEXT_DATA__ returns None -> falls through to DOM path, _try_day IS called."""
    page = AsyncMock()
    page.goto = AsyncMock()
    # __NEXT_DATA__ element not found
    page.evaluate = AsyncMock(return_value=None)
    page.wait_for_selector = AsyncMock()
    page.wait_for_timeout = AsyncMock()

    worker = _make_worker(page=page)
    worker._try_day = AsyncMock(return_value=True)

    result = await worker._poll()

    assert result is True
    worker._try_day.assert_awaited_once()
    page.wait_for_timeout.assert_not_awaited()


@pytest.mark.asyncio
async def test_capture_available_dates_waits_for_day_buttons_not_fixed_sleep():
    from sniper import _capture_available_dates

    page = AsyncMock()
    button = AsyncMock()
    button.get_attribute = AsyncMock(return_value="2026-04-18")
    page.query_selector_all = AsyncMock(return_value=[button])

    dates = await _capture_available_dates(page, "taneda", "1")

    assert dates == {"2026-04-18"}
    assert page.wait_for_selector.await_args_list[0].args[0] == "div.ConsumerCalendar-month"
    assert page.wait_for_selector.await_args_list[1].args[0] == "button[data-testid='consumer-calendar-day']"
    page.wait_for_timeout.assert_not_awaited()


# ── _any_slot_in_window tests ────────────────────────────────────────────────

class TestAnySlotInWindow:
    """Test _any_slot_in_window() boundary logic."""

    def test_slot_in_window(self):
        worker = _make_worker()  # window 5:00 PM - 9:30 PM
        assert worker._any_slot_in_window(["7:00 PM"]) is True

    def test_slot_at_earliest_boundary(self):
        worker = _make_worker()
        assert worker._any_slot_in_window(["5:00 PM"]) is True

    def test_slot_at_latest_boundary(self):
        worker = _make_worker()
        assert worker._any_slot_in_window(["9:30 PM"]) is True

    def test_slot_before_window(self):
        worker = _make_worker()
        assert worker._any_slot_in_window(["4:59 PM"]) is False

    def test_slot_after_window(self):
        worker = _make_worker()
        assert worker._any_slot_in_window(["9:31 PM"]) is False

    def test_mixed_slots_one_match(self):
        worker = _make_worker()
        assert worker._any_slot_in_window(["3:00 PM", "10:00 PM", "6:00 PM"]) is True

    def test_empty_list(self):
        worker = _make_worker()
        assert worker._any_slot_in_window([]) is False

    def test_unparseable_times_skipped(self):
        worker = _make_worker()
        assert worker._any_slot_in_window(["garbage", "not-a-time"]) is False

    def test_unparseable_mixed_with_valid(self):
        worker = _make_worker()
        assert worker._any_slot_in_window(["garbage", "7:00 PM"]) is True
