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

from models import Task, Target, RESERVATION_TIME_FORMAT
from notifier import notify_user

log = logging.getLogger(__name__)

# ── Configuration ─────────────────────────────────────────────────────────────

CHROME_EXECUTABLE = os.environ.get("CHROME_EXECUTABLE") or None
# Set PLAYWRIGHT_HEADLESS=1 in environments without a display (CI, containers)
HEADLESS = os.environ.get("PLAYWRIGHT_HEADLESS", "0") == "1"

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
    """Polls one (restaurant, target) combination inside a shared browser context."""

    def __init__(
        self,
        task: Task,
        target: Target,
        page: Page,
        found_event: asyncio.Event,
        dry_run: bool = False,
    ):
        self.task        = task
        self.target      = target
        self.page        = page
        self.found_event = found_event
        self.dry_run     = dry_run
        self.checkout_url: Optional[str] = None

    async def run(self) -> None:
        log.info(
            "[%s/%s] Worker started | party=%s | %s–%s",
            self.task.url, self.target.date,
            self.task.size, self.target.earliest_time, self.target.latest_time,
        )
        try:
            while not self.found_event.is_set():
                delay = REFRESH_DELAY_SEC + random.uniform(-JITTER_SEC, JITTER_SEC)
                await asyncio.sleep(max(0.1, delay))
                if await self._poll():
                    self.found_event.set()
                    msg = (
                        f"Restaurant : {self.task.url}\n"
                        f"  Date     : {self.target.date}\n"
                        f"  Party    : {self.task.size}\n"
                        f"  Window   : {self.target.earliest_time} – {self.target.latest_time}"
                    )
                    notify_user(msg, hold_minutes=BROWSER_HOLD_SEC // 60)
                    if not self.dry_run:
                        log.info(
                            "[%s/%s] Holding browser for %ds — finish checkout now!",
                            self.task.url, self.target.date, BROWSER_HOLD_SEC,
                        )
                        await asyncio.sleep(BROWSER_HOLD_SEC)
                    break
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            log.error("[%s/%s] Unexpected error: %s", self.task.url, self.target.date, exc)

    # ── Poll cycle ────────────────────────────────────────────────────────────

    async def _poll(self) -> bool:
        """Load search page and look for an available slot on self.target.date."""
        try:
            await self.page.goto(
                self.target.search_url(self.task.url, self.task.size),
                wait_until="domcontentloaded",
                timeout=PAGE_LOAD_TIMEOUT,
            )
            await self.page.wait_for_selector(
                "div.ConsumerCalendar-month",
                timeout=PAGE_LOAD_TIMEOUT,
            )
        except PWTimeout:
            log.debug("[%s/%s] Page timeout, retrying", self.task.url, self.target.date)
            return False
        except Exception as e:
            log.warning("[%s/%s] Page load error: %s", self.task.url, self.target.date, e)
            return False

        # Extra settle time for React to render day buttons after calendar container appears
        await self.page.wait_for_timeout(2000)
        return await self._try_day()

    async def _try_day(self) -> bool:
        """Click the target day using its aria-label date attribute."""
        try:
            btn = await self.page.query_selector(
                f"button[data-testid='consumer-calendar-day']"
                f"[aria-label='{self.target.date}'][aria-disabled='false']"
            )
            if btn:
                log.info("[%s/%s] Day available — clicking", self.task.url, self.target.date)
                await btn.click()
                return await self._try_time()
            log.debug("[%s/%s] Day %s not available", self.task.url, self.target.date, self.target.date)
        except Exception as e:
            log.debug("[%s/%s] Error in _try_day: %s", self.task.url, self.target.date, e)
        return False

    async def _try_time(self) -> bool:
        """
        After a day is selected, find a search-result card whose time falls
        within the task window and click its 'Book' button.

        Each card uses:
          data-testid="search-result"          — the card container
          data-testid="search-result-time"     — the time label (e.g. "5:30 PM")
          data-testid="booking-card-button"    — the Book action button
        """
        try:
            await self.page.wait_for_selector(
                "[data-testid='search-result']",
                timeout=SLOT_WAIT_TIMEOUT,
            )
            cards = await self.page.query_selector_all("[data-testid='search-result']")
            for card in cards:
                time_el = await card.query_selector("[data-testid='search-result-time']")
                if not time_el:
                    continue
                time_text = (await time_el.inner_text()).strip()
                try:
                    t = datetime.strptime(time_text, RESERVATION_TIME_FORMAT)
                except ValueError:
                    continue
                if self.target.earliest_dt() <= t <= self.target.latest_dt():
                    book_btn = await card.query_selector(
                        "button[data-testid='booking-card-button']"
                    )
                    if not book_btn:
                        continue
                    log.info("[%s/%s] Clicking slot %s", self.task.url, self.target.date, time_text)
                    if not self.dry_run:
                        await book_btn.click()
                        try:
                            await self.page.wait_for_url("**/checkout/**", timeout=8000)
                        except PWTimeout:
                            pass
                        self.checkout_url = self.page.url
                    else:
                        self.checkout_url = "(dry-run)"
                    return True
        except PWTimeout:
            log.debug("[%s/%s] No search-result cards loaded", self.task.url, self.target.date)
        except Exception as e:
            log.debug("[%s/%s] Error in _try_time: %s", self.task.url, self.target.date, e)
        return False


# ── Cookie helpers ────────────────────────────────────────────────────────────

def _parse_netscape_cookies(lines) -> list:
    """Parse Netscape-format cookie file lines into Playwright cookie dicts."""
    cookies = []
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split("\t")
        if len(parts) < 7:
            continue
        domain, _, path, secure, expires, name, value = parts[:7]
        cookies.append({
            "name":     name,
            "value":    value,
            "domain":   domain,
            "path":     path,
            "secure":   secure.upper() == "TRUE",
            "httpOnly": False,
            "sameSite": "Lax",
        })
    return cookies


async def _load_cookies(context: BrowserContext, cookies_file: str) -> None:
    with open(cookies_file) as f:
        cookies = _parse_netscape_cookies(f.readlines())
    if cookies:
        await context.add_cookies(cookies)
        log.info("Loaded %d cookies from %s", len(cookies), cookies_file)
    else:
        log.warning("No cookies parsed from %s", cookies_file)


async def _interactive_login(context: BrowserContext, page_load_timeout: int) -> None:
    """Open Tock login page and wait for the user to log in manually."""
    import sys
    page = await context.new_page()
    await _apply_stealth(page)
    await page.goto("https://www.exploretock.com/login", timeout=page_load_timeout)
    print(
        "\n[AUTH] Browser is open — log in to Tock, then press Enter here to continue...",
        file=sys.stderr,
    )
    try:
        await page.wait_for_url(
            lambda url: "/login" not in url,
            timeout=300_000,  # 5 min
        )
        log.info("Login detected — continuing.")
    except Exception:
        log.warning("Login timeout or error — continuing anyway.")
    await page.close()


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

async def snipe_task(
    task: Task,
    dry_run: bool = False,
    interval: float = 30.0,
    max_duration: float = 0,
    release_at=None,
    cookies_file=None,
    interactive_login: bool = False,
    prompt_login: bool = False,
) -> Optional[dict]:
    """
    Open one browser for *task*, one tab per target, and poll concurrently.
    Returns a result dict on success, or None if no reservation was secured.
    """
    found_event = asyncio.Event()
    log.info(
        "[%s] Starting snipe — %d target(s) to watch",
        task.url, len(task.targets),
    )

    async with async_playwright() as p:
        _launch_kwargs: dict = {"headless": HEADLESS, "args": ["--disable-blink-features=AutomationControlled"]}
        if CHROME_EXECUTABLE:
            _launch_kwargs["executable_path"] = CHROME_EXECUTABLE
        browser: Browser = await p.chromium.launch(**_launch_kwargs)
        context: BrowserContext = await browser.new_context(user_agent=USER_AGENT)

        # Optional login (shared session across all tabs)
        if ENABLE_LOGIN:
            login_page = await context.new_page()
            await _apply_stealth(login_page)
            await _login(login_page)
            await login_page.close()

        if cookies_file:
            await _load_cookies(context, cookies_file)
        if interactive_login:
            await _interactive_login(context, PAGE_LOAD_TIMEOUT)

        # Open one tab per target
        workers: List[DayWorker] = []
        for i, target in enumerate(task.targets):
            page = await context.new_page()
            await _apply_stealth(page)
            workers.append(DayWorker(task, target, page, found_event, dry_run=dry_run))
            if i < len(task.targets) - 1:
                await asyncio.sleep(LAUNCH_STAGGER_SEC)

        # Run all workers concurrently
        await asyncio.gather(*[w.run() for w in workers], return_exceptions=True)

        await browser.close()

    if not found_event.is_set():
        return None

    # Find the winning worker (the one that captured a checkout URL, or any that fired the event)
    winning_worker = next(
        (w for w in workers if w.checkout_url is not None),
        workers[0] if workers else None,
    )
    if winning_worker is None:
        return None

    return {
        "restaurant":   task.url,
        "date":         winning_worker.target.date,
        "checkout_url": winning_worker.checkout_url or "",
    }


async def snipe_all(tasks: List[Task], **kwargs) -> List[dict]:
    """Run multiple restaurant tasks concurrently, returning a list of result dicts."""
    log.info(
        "Sniping %d restaurant(s) | %d total target-workers",
        len(tasks), sum(len(t.targets) for t in tasks),
    )
    raw = await asyncio.gather(
        *[snipe_task(t, **kwargs) for t in tasks],
        return_exceptions=True,
    )
    results = []
    for task, outcome in zip(tasks, raw):
        if isinstance(outcome, Exception):
            log.error("[%s] Task failed: %s", task.url, outcome)
            results.append({
                "status": "error",
                "restaurant": task.url,
                "error": str(outcome),
                "date": "",
                "checkout_url": "",
            })
        elif outcome:
            log.info("[%s] Reservation secured! checkout=%s", task.url, outcome.get("checkout_url"))
            results.append({"status": "success", **outcome})
        else:
            log.info("[%s] No reservation found in this run.", task.url)
            results.append({
                "status": "no_slots",
                "restaurant": task.url,
                "date": "",
                "checkout_url": "",
            })
    return results
