"""
Async Playwright sniper — concurrent reservation cart-holder.

Architecture
------------
One Browser is opened per restaurant Task.  Inside that browser, one Page
(tab) is opened per target day.  All pages poll concurrently via asyncio;
the first page that clicks a valid time slot:

  1. Sets the shared asyncio.Event so all other tabs stop.
  2. Notifies the user (console banner + desktop popup + beep).
  3. Keeps the browser window open for BROWSER_HOLD_SEC so the user can
     complete checkout — Tock holds the cart in-session for ~10 minutes.

Cloudflare / bot-detection mitigations
---------------------------------------
  - Non-headless Chrome (Turnstile is most aggressive in headless mode)
  - playwright-stealth applied per page (masks navigator.webdriver, etc.)
  - Randomised poll delay ± jitter
  - Custom User-Agent matching a real Chrome version
"""

import asyncio
import logging
import os
import random
from datetime import datetime
from typing import List, Optional

from playwright.async_api import (
    async_playwright,
    Browser,
    BrowserContext,
    Page,
    TimeoutError as PWTimeout,
)

from models import Task, RESERVATION_TIME_FORMAT, MONTH_NUM
from notifier import notify_user

log = logging.getLogger(__name__)

# ── Configuration ─────────────────────────────────────────────────────────────

CHROME_EXECUTABLE = os.environ.get(
    "CHROME_EXECUTABLE",
    "/root/.cache/ms-playwright/chromium-1194/chrome-linux/chrome",
)

REFRESH_DELAY_SEC  = float(os.environ.get("REFRESH_DELAY_SEC", "1.0"))
JITTER_SEC         = 0.3       # ± random jitter on top of REFRESH_DELAY_SEC
PAGE_LOAD_TIMEOUT  = 15_000    # ms
SLOT_WAIT_TIMEOUT  = 8_000     # ms — wait for time slots after clicking a day
BROWSER_HOLD_SEC   = 600       # keep browser alive after securing cart (10 min)
LAUNCH_STAGGER_SEC = 0.5       # delay between spinning up worker tabs

ENABLE_LOGIN   = False
TOCK_USERNAME  = os.environ.get("TOCK_USERNAME", "")
TOCK_PASSWORD  = os.environ.get("TOCK_PASSWORD", "")

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)


# ── Stealth helper ────────────────────────────────────────────────────────────

async def _apply_stealth(page: Page) -> None:
    try:
        from playwright_stealth import stealth_async  # type: ignore
        await stealth_async(page)
    except ImportError:
        # Fallback: manual WebDriver flag removal
        await page.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
        )


# ── Per-day worker ────────────────────────────────────────────────────────────

class DayWorker:
    """Polls one (restaurant, day) combination inside a shared browser context."""

    def __init__(
        self,
        task: Task,
        day: str,
        page: Page,
        found_event: asyncio.Event,
        dry_run: bool = False,
    ):
        self.task        = task
        self.day         = day
        self.page        = page
        self.found_event = found_event
        self.dry_run     = dry_run

    async def run(self) -> None:
        log.info(
            "[%s/%s/%s] Worker started | party=%s | %s–%s",
            self.task.url, self.task.month[:3], self.day,
            self.task.size, self.task.earliest_time, self.task.latest_time,
        )
        try:
            while not self.found_event.is_set():
                delay = REFRESH_DELAY_SEC + random.uniform(-JITTER_SEC, JITTER_SEC)
                await asyncio.sleep(max(0.1, delay))
                if await self._poll():
                    self.found_event.set()
                    msg = (
                        f"Restaurant : {self.task.url}\n"
                        f"  Date     : {self.task.month} {self.day}, {self.task.year}\n"
                        f"  Party    : {self.task.size}\n"
                        f"  Window   : {self.task.earliest_time} – {self.task.latest_time}"
                    )
                    notify_user(msg, hold_minutes=BROWSER_HOLD_SEC // 60)
                    if not self.dry_run:
                        log.info(
                            "[%s/%s] Holding browser for %ds — finish checkout now!",
                            self.task.url, self.day, BROWSER_HOLD_SEC,
                        )
                        await asyncio.sleep(BROWSER_HOLD_SEC)
                    break
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            log.error("[%s/%s] Unexpected error: %s", self.task.url, self.day, exc)

    # ── Poll cycle ────────────────────────────────────────────────────────────

    async def _poll(self) -> bool:
        """Load search page and look for an available slot on self.day."""
        try:
            await self.page.goto(
                self.task.search_url(),
                wait_until="domcontentloaded",
                timeout=PAGE_LOAD_TIMEOUT,
            )
            await self.page.wait_for_selector(
                "div.ConsumerCalendar-month",
                timeout=PAGE_LOAD_TIMEOUT,
            )
        except PWTimeout:
            log.debug("[%s/%s] Page timeout, retrying", self.task.url, self.day)
            return False
        except Exception as e:
            log.warning("[%s/%s] Page load error: %s", self.task.url, self.day, e)
            return False

        return await self._try_day()

    async def _try_day(self) -> bool:
        try:
            month_els = await self.page.query_selector_all("div.ConsumerCalendar-month")
            month_el  = None

            for el in month_els:
                heading = await el.query_selector(
                    "div.ConsumerCalendar-monthHeading span.H1"
                )
                if heading:
                    text = (await heading.inner_text()).lower()
                    if self.task.month.lower() in text:
                        month_el = el
                        break

            if month_el is None:
                log.debug("[%s/%s] Month %s not in calendar", self.task.url, self.day, self.task.month)
                return False

            day_btns = await month_el.query_selector_all(
                "button.ConsumerCalendar-day.is-in-month.is-available"
            )
            for btn in day_btns:
                span = await btn.query_selector("span.B2")
                if not span:
                    continue
                if (await span.inner_text()).strip() == self.day:
                    log.info("[%s/%s] Day available — clicking", self.task.url, self.day)
                    await btn.click()
                    return await self._try_time()

        except Exception as e:
            log.debug("[%s/%s] Error in _try_day: %s", self.task.url, self.day, e)

        return False

    async def _try_time(self) -> bool:
        try:
            await self.page.wait_for_selector(
                "button.Consumer-resultsListItem.is-available",
                timeout=SLOT_WAIT_TIMEOUT,
            )
            slots = await self.page.query_selector_all(
                "button.Consumer-resultsListItem.is-available"
            )
            for slot in slots:
                ts = await slot.query_selector("span.Consumer-resultsListItemTime span")
                if not ts:
                    continue
                try:
                    t = datetime.strptime((await ts.inner_text()).strip(), RESERVATION_TIME_FORMAT)
                except ValueError:
                    continue
                if self.task.formatted_earliest() <= t <= self.task.formatted_latest():
                    log.info(
                        "[%s/%s] Clicking slot %s",
                        self.task.url, self.day, (await ts.inner_text()).strip(),
                    )
                    if not self.dry_run:
                        await slot.click()
                    return True
        except PWTimeout:
            log.debug("[%s/%s] No time slots loaded", self.task.url, self.day)
        except Exception as e:
            log.debug("[%s/%s] Error in _try_time: %s", self.task.url, self.day, e)

        return False


# ── Login ─────────────────────────────────────────────────────────────────────

async def _login(page: Page) -> None:
    log.info("Logging into Tock...")
    await page.goto("https://www.exploretock.com/login", timeout=PAGE_LOAD_TIMEOUT)
    await page.fill("input[name='email']",    TOCK_USERNAME)
    await page.fill("input[name='password']", TOCK_PASSWORD)
    await page.click(".Button")
    await page.wait_for_selector(".MainHeader-accountName", timeout=PAGE_LOAD_TIMEOUT)
    log.info("Login successful")


# ── Task orchestration ────────────────────────────────────────────────────────

async def snipe_task(task: Task, dry_run: bool = False) -> bool:
    """
    Open one browser for *task*, one tab per target day, and poll concurrently.
    Returns True if a reservation was secured.
    """
    found_event = asyncio.Event()
    log.info(
        "[%s] Starting snipe — %d day(s) to watch in %s %s",
        task.url, len(task.days), task.month, task.year,
    )

    async with async_playwright() as p:
        browser: Browser = await p.chromium.launch(
            executable_path=CHROME_EXECUTABLE,
            headless=False,
            args=["--disable-blink-features=AutomationControlled"],
        )
        context: BrowserContext = await browser.new_context(user_agent=USER_AGENT)

        # Optional login (shared session across all tabs)
        if ENABLE_LOGIN:
            login_page = await context.new_page()
            await _apply_stealth(login_page)
            await _login(login_page)
            await login_page.close()

        # Open one tab per target day
        workers: List[DayWorker] = []
        for i, day in enumerate(task.days):
            page = await context.new_page()
            await _apply_stealth(page)
            workers.append(DayWorker(task, day, page, found_event, dry_run=dry_run))
            if i < len(task.days) - 1:
                await asyncio.sleep(LAUNCH_STAGGER_SEC)

        # Run all workers concurrently
        await asyncio.gather(*[w.run() for w in workers], return_exceptions=True)

        if not dry_run:
            await browser.close()
        else:
            # In dry-run, close immediately after checking
            await browser.close()

    return found_event.is_set()


async def snipe_all(tasks: List[Task], dry_run: bool = False) -> None:
    """Run multiple restaurant tasks concurrently."""
    log.info(
        "Sniping %d restaurant(s) | %d total day-workers",
        len(tasks), sum(len(t.days) for t in tasks),
    )
    results = await asyncio.gather(
        *[snipe_task(t, dry_run=dry_run) for t in tasks],
        return_exceptions=True,
    )
    for task, result in zip(tasks, results):
        if isinstance(result, Exception):
            log.error("[%s] Task failed: %s", task.url, result)
        elif result:
            log.info("[%s] Reservation secured!", task.url)
        else:
            log.info("[%s] No reservation found in this run.", task.url)
