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
import re
import sys
from datetime import datetime
from zoneinfo import ZoneInfo
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

PAGE_LOAD_TIMEOUT  = 15_000    # ms
SLOT_WAIT_TIMEOUT  = 8_000     # ms — wait for time slots after clicking a day
BROWSER_HOLD_SEC   = 600       # keep browser alive after securing cart (10 min)
LAUNCH_STAGGER_SEC = 0.5       # delay between spinning up worker tabs

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
        interval: float = 30.0,
        prompt_login: bool = False,
    ):
        self.task         = task
        self.target       = target
        self.page         = page
        self.found_event  = found_event
        self.dry_run      = dry_run
        self.interval     = interval
        self.prompt_login = prompt_login
        self.checkout_url: Optional[str] = None
        self.matched_time: Optional[str] = None

    async def run(self) -> None:
        log.info(
            "[%s/%s] Worker started | party=%s | %s–%s",
            self.task.url, self.target.date,
            self.task.size, self.target.earliest_time, self.target.latest_time,
        )
        try:
            while not self.found_event.is_set():
                if await self._poll():
                    self.found_event.set()
                    msg = (
                        f"Restaurant : {self.task.url}\n"
                        f"  Date     : {self.target.date}\n"
                        f"  Party    : {self.task.size}\n"
                        f"  Window   : {self.target.earliest_time} – {self.target.latest_time}"
                    )
                    notify_user(msg, hold_minutes=BROWSER_HOLD_SEC // 60)
                    if self.prompt_login and not self.dry_run:
                        await _prompt_and_login(self.page, self.task.url)
                    if not self.dry_run:
                        log.info(
                            "[%s/%s] Holding browser for %ds — finish checkout now!",
                            self.task.url, self.target.date, BROWSER_HOLD_SEC,
                        )
                        await asyncio.sleep(BROWSER_HOLD_SEC)
                    break
                # Sleep between polls (not before the first one)
                jitter = random.uniform(
                    -min(self.interval * 0.1, 2.0),
                    min(self.interval * 0.1, 2.0),
                )
                delay = max(0.5, self.interval + jitter)
                await asyncio.sleep(delay)
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            log.error("[%s/%s] Unexpected error: %s", self.task.url, self.target.date, exc)

    # ── Poll cycle ────────────────────────────────────────────────────────────

    def _any_slot_in_window(self, time_strings: list) -> bool:
        """Check if any time string falls within the target's earliest/latest window."""
        for t_str in time_strings:
            try:
                t = datetime.strptime(t_str, RESERVATION_TIME_FORMAT)
                if self.target.earliest_dt() <= t <= self.target.latest_dt():
                    return True
            except ValueError:
                continue
        return False

    async def _poll(self) -> bool:
        """Load search page and look for an available slot on self.target.date."""
        try:
            await self.page.goto(
                self.target.search_url(self.task.url, self.task.size),
                wait_until="domcontentloaded",
                timeout=PAGE_LOAD_TIMEOUT,
            )
        except PWTimeout:
            log.debug("[%s/%s] Page timeout, retrying", self.task.url, self.target.date)
            return False
        except Exception as e:
            log.warning("[%s/%s] Page load error: %s", self.task.url, self.target.date, e)
            return False

        # Fast pre-filter: check __NEXT_DATA__ before waiting for full DOM
        next_data_slots = await self._extract_next_data()
        if next_data_slots is not None:
            if not self._any_slot_in_window(next_data_slots):
                log.debug(
                    "[%s/%s] __NEXT_DATA__ shows %d slot(s) but none in window — skipping DOM",
                    self.task.url, self.target.date, len(next_data_slots),
                )
                return False
            log.info(
                "[%s/%s] __NEXT_DATA__ has matching slot(s) — proceeding to DOM path",
                self.task.url, self.target.date,
            )

        try:
            await self.page.wait_for_selector(
                "div.ConsumerCalendar-month",
                timeout=PAGE_LOAD_TIMEOUT,
            )
        except PWTimeout:
            log.debug("[%s/%s] Calendar selector timeout, retrying", self.task.url, self.target.date)
            return False
        except Exception as e:
            log.warning("[%s/%s] Calendar load error: %s", self.task.url, self.target.date, e)
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
                    self.matched_time = time_text
                    if not self.dry_run:
                        await book_btn.click()
                        cart_confirmed = False
                        try:
                            await self.page.wait_for_url("**/checkout/**", timeout=8000)
                            cart_confirmed = True
                        except PWTimeout:
                            # Fallback checks per PRD FR-8
                            holding = await self.page.query_selector("[data-testid='holding-time']")
                            if holding:
                                cart_confirmed = True
                            else:
                                complete_el = await self.page.query_selector("text='Complete your reservation'")
                                if complete_el:
                                    cart_confirmed = True

                        if cart_confirmed:
                            self.checkout_url = self.page.url
                            return True
                        else:
                            log.warning(
                                "[%s/%s] Clicked slot %s but cart add not confirmed — retrying next cycle",
                                self.task.url, self.target.date, time_text,
                            )
                            return False
                    else:
                        self.checkout_url = "(dry-run)"
                        return True
        except PWTimeout:
            log.debug("[%s/%s] No search-result cards loaded", self.task.url, self.target.date)
        except Exception as e:
            log.debug("[%s/%s] Error in _try_time: %s", self.task.url, self.target.date, e)
        return False

    # ── __NEXT_DATA__ extraction ─────────────────────────────────────────────

    # Keys in __NEXT_DATA__.props.pageProps to search for availability data
    _AVAILABILITY_KEYS = (
        "availabilities", "availability", "searchResults",
        "experiences", "timeslots", "slots",
    )

    # Fields whose values are treated as time strings
    _TIME_FIELDS = {"time", "dateTime", "startTime", "start_time", "startDate"}

    async def _extract_next_data(self) -> Optional[List[str]]:
        """Extract availability times from __NEXT_DATA__ JSON embedded in the page.

        Returns a deduplicated list of "H:MM AM/PM" time strings, or None if
        no data could be extracted.
        """
        try:
            raw = await self.page.evaluate(
                """() => {
                    const el = document.querySelector('#__NEXT_DATA__');
                    if (!el) return null;
                    try { return JSON.parse(el.textContent); }
                    catch { return null; }
                }"""
            )
        except Exception:
            return None

        if not raw:
            return None

        try:
            page_props = raw.get("props", {}).get("pageProps", {})
        except (AttributeError, TypeError):
            return None

        # Collect candidates from all known paths (scan all, not first-match)
        candidates: list = []

        # Direct keys under pageProps
        for key in self._AVAILABILITY_KEYS:
            val = page_props.get(key)
            if val is not None:
                candidates.append(val)

        # initialData.availability / initialData.searchResults
        initial = page_props.get("initialData")
        if isinstance(initial, dict):
            for key in ("availability", "searchResults"):
                val = initial.get(key)
                if val is not None:
                    candidates.append(val)

        # dehydratedState.queries[0].state.data (React Query hydration)
        dehydrated = page_props.get("dehydratedState")
        if isinstance(dehydrated, dict):
            queries = dehydrated.get("queries")
            if isinstance(queries, list) and queries:
                state = queries[0].get("state", {}) if isinstance(queries[0], dict) else {}
                if "data" in state:
                    candidates.append(state["data"])

        if not candidates:
            return None

        times: list = []
        for candidate in candidates:
            self._collect_times(candidate, times)

        if not times:
            return None

        # Deduplicate while preserving order
        seen: set = set()
        result: list = []
        for t in times:
            if t not in seen:
                seen.add(t)
                result.append(t)
        return result

    def _collect_times(self, obj, out: list) -> None:
        """Recursively collect formatted time values from *obj*."""
        if isinstance(obj, dict):
            for key, value in obj.items():
                if key in self._TIME_FIELDS and isinstance(value, str):
                    fmt = self._format_time(value)
                    if fmt:
                        out.append(fmt)
                else:
                    self._collect_times(value, out)
        elif isinstance(obj, list):
            for item in obj:
                self._collect_times(item, out)

    @staticmethod
    def _format_time(raw: str) -> Optional[str]:
        """Convert a raw time string to 'H:MM AM/PM' format.

        Handles:
          - ISO datetime: '2026-03-15T17:00' or '2026-03-15T17:00:00'
          - 24-hour: '17:00'
          - 12-hour passthrough: '5:00 PM'
        """
        if not raw:
            return None

        raw = raw.strip()

        # 12-hour passthrough (already in target format)
        if re.search(r'[AaPp][Mm]', raw):
            try:
                dt = datetime.strptime(raw, "%I:%M %p")
                return dt.strftime("%-I:%M %p")
            except ValueError:
                return None

        # ISO datetime (contains 'T')
        if 'T' in raw:
            time_part = raw.split('T', 1)[1]
            # Strip seconds and timezone if present
            time_part = time_part[:5]
        else:
            time_part = raw[:5]

        # Parse as 24-hour
        try:
            dt = datetime.strptime(time_part, "%H:%M")
            return dt.strftime("%-I:%M %p")
        except ValueError:
            return None


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
    try:
        with open(cookies_file) as f:
            lines = f.readlines()
    except (FileNotFoundError, PermissionError) as e:
        log.error("Cannot read cookies file '%s': %s", cookies_file, e)
        print(f"[AUTH] Error: cannot read cookies file: {e}", file=sys.stderr)
        return
    cookies = _parse_netscape_cookies(lines)
    if cookies:
        await context.add_cookies(cookies)
        log.info("Loaded %d cookies from %s", len(cookies), cookies_file)
    else:
        log.warning("No cookies parsed from %s — proceeding unauthenticated", cookies_file)
        print(f"[AUTH] Warning: no cookies found in '{cookies_file}' — proceeding unauthenticated", file=sys.stderr)


async def _interactive_login(context: BrowserContext, page_load_timeout: int) -> None:
    """Open Tock login page and wait for the user to log in manually."""
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


async def _prompt_and_login(page: Page, restaurant_slug: str) -> None:
    """After cart add: prompt for Tock credentials via stdin and log in."""
    print(
        f"\n[AUTH] Slot secured! To tie this cart to your Tock account:\n"
        f"  Enter credentials below (or Ctrl+C to skip).\n"
        f"  Alternatively, open exploretock.com/{restaurant_slug} in your browser\n"
        f"  and log in — the cart will be waiting there.\n",
        file=sys.stderr,
    )
    try:
        import getpass
        email    = input("  Tock email: ")
        password = getpass.getpass("  Tock password: ")
    except (KeyboardInterrupt, EOFError):
        print("\n[AUTH] Skipped — log in manually to complete checkout.", file=sys.stderr)
        return

    if not email.strip() or not password:
        print("[AUTH] No credentials entered — skipping.", file=sys.stderr)
        return

    try:
        await page.goto("https://www.exploretock.com/login", timeout=PAGE_LOAD_TIMEOUT)
        await page.fill("input[name='email']",    email.strip())
        await page.fill("input[name='password']", password)
        await page.click("button[type='submit']")
        try:
            await page.wait_for_url(
                lambda url: "/login" not in url,
                timeout=15_000,
            )
        except Exception:
            pass
        checkout = f"https://www.exploretock.com/{restaurant_slug}"
        log.info("Login complete. Cart available at: %s", checkout)
        print(f"\n[AUTH] Logged in! Cart ready at: {checkout}", file=sys.stderr)
    except Exception as e:
        log.warning("Login attempt failed: %s", e)
        print(f"[AUTH] Login failed ({e}). Log in manually.", file=sys.stderr)


# ── Release-time scheduler ────────────────────────────────────────────────────

async def _wait_for_release(release_at: str, timezone: str = None) -> None:
    """Sleep until release_at HH:MM (24h). Uses *timezone* if provided, else local time. Pre-warms 30s before."""
    try:
        tz = ZoneInfo(timezone) if timezone else None
        now = datetime.now(tz=tz)
        target = datetime.strptime(release_at, "%H:%M").replace(
            year=now.year, month=now.month, day=now.day,
            tzinfo=tz,
        )
    except ValueError:
        log.error("Invalid --release-at format '%s'. Use HH:MM (24h), e.g. '10:00'", release_at)
        return

    if target <= datetime.now(tz=tz):
        log.info("Release time %s already passed — firing immediately.", release_at)
        return

    pre_warm_secs = (target - datetime.now(tz=tz)).total_seconds() - 30
    if pre_warm_secs > 0:
        log.info(
            "Sleeping %.0fs until pre-warm window (30s before %s)...",
            pre_warm_secs, release_at,
        )
        await asyncio.sleep(pre_warm_secs)

    remaining = (target - datetime.now(tz=tz)).total_seconds()
    if remaining > 0:
        log.info("Waiting %.1fs for release at %s — get ready!", remaining, release_at)
        await asyncio.sleep(remaining)

    log.info("RELEASE TIME %s — firing all workers!", release_at)


# ── Task orchestration ────────────────────────────────────────────────────────

async def snipe_task(
    task: Task,
    dry_run: bool = False,
    interval: float = 30.0,
    max_duration: float = 0,
    release_at=None,
    timezone: str = None,
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

        if cookies_file:
            await _load_cookies(context, cookies_file)
        if interactive_login:
            await _interactive_login(context, PAGE_LOAD_TIMEOUT)

        # Open one tab per target
        workers: List[DayWorker] = []
        for i, target in enumerate(task.targets):
            page = await context.new_page()
            await _apply_stealth(page)
            workers.append(DayWorker(task, target, page, found_event, dry_run=dry_run, interval=interval, prompt_login=prompt_login))
            if i < len(task.targets) - 1:
                await asyncio.sleep(LAUNCH_STAGGER_SEC)

        # Pre-warm all pages in release mode so they're loaded before the countdown ends
        if release_at:
            log.info("[%s] Pre-warming %d worker pages...", task.url, len(workers))
            for w in workers:
                try:
                    await w.page.goto(
                        w.target.search_url(task.url, task.size),
                        wait_until="domcontentloaded",
                        timeout=PAGE_LOAD_TIMEOUT,
                    )
                except Exception as e:
                    log.warning("[%s/%s] Pre-warm failed: %s", task.url, w.target.date, e)

            await _wait_for_release(release_at, timezone=timezone)

        # Run all workers concurrently
        timed_out = False
        gather_coro = asyncio.gather(*[w.run() for w in workers], return_exceptions=True)
        if max_duration > 0:
            try:
                await asyncio.wait_for(gather_coro, timeout=max_duration * 60)
            except asyncio.TimeoutError:
                log.info("[%s] max-duration %.0fmin reached — stopping.", task.url, max_duration)
                timed_out = True
                found_event.set()  # signal all workers to stop their loops
        else:
            await gather_coro

        await browser.close()

    # Check if any worker actually found a slot (not just timed out)
    winning_worker = next(
        (w for w in workers if w.checkout_url is not None),
        None,
    )
    if winning_worker is None:
        return None

    return {
        "restaurant":   task.url,
        "date":         winning_worker.target.date,
        "time":         winning_worker.matched_time or "",
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
                "time": "",
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
                "time": "",
                "checkout_url": "",
            })
    return results
