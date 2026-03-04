"""
Recon agent — discovers available dates and times for a Tock restaurant.

Uses Playwright (+ stealth) to browse the reservation calendar and extract:
  - Which months/days have open availability
  - What time slots exist

Optionally uses Claude (Anthropic API) to produce a richer analysis when
ANTHROPIC_API_KEY is set in the environment.

Usage
-----
  python recon.py <restaurant-slug> [--size 2] [--save config.json]
"""

import asyncio
import json
import logging
import os
import sys
from datetime import datetime
from typing import Dict, List, Optional, Tuple

from playwright.async_api import async_playwright, Browser, BrowserContext, Page, TimeoutError as PWTimeout

from models import Task, MONTH_NUM, RESERVATION_TIME_FORMAT

log = logging.getLogger(__name__)

# ── Config ────────────────────────────────────────────────────────────────────

CHROME_EXECUTABLE = os.environ.get(
    "CHROME_EXECUTABLE",
    "/root/.cache/ms-playwright/chromium-1194/chrome-linux/chrome",
)
PAGE_LOAD_TIMEOUT_MS = 15_000
DEFAULT_PARTY_SIZE   = "2"


# ── Browser helpers ───────────────────────────────────────────────────────────

async def _new_stealth_page(context: BrowserContext) -> Page:
    """Open a new page with stealth patches applied."""
    page = await context.new_page()
    try:
        from playwright_stealth import stealth_async  # type: ignore
        await stealth_async(page)
    except ImportError:
        log.debug("playwright-stealth not installed; running without stealth patches")
    return page


# ── Core scraping ─────────────────────────────────────────────────────────────

async def _scrape_restaurant(slug: str, size: str) -> Dict[str, dict]:
    """
    Open the Tock search page for *slug* and extract available months/days.
    Returns a dict keyed by month name:
        { "March": { "year": "2026", "days": ["01","14"], "time_slots": ["5:00 PM", ...] } }
    """
    month_n = f"{datetime.now().month:02d}"
    year    = str(datetime.now().year)
    url     = (
        f"https://www.exploretock.com/{slug}/search"
        f"?date={year}-{month_n}-01&size={size}&time=19%3A00"
    )

    result: Dict[str, dict] = {}

    async with async_playwright() as p:
        browser: Browser = await p.chromium.launch(
            executable_path=CHROME_EXECUTABLE,
            headless=False,  # non-headless helps with Cloudflare Turnstile
            args=["--disable-blink-features=AutomationControlled"],
        )
        context = await browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            )
        )
        page = await _new_stealth_page(context)

        try:
            log.info("[recon] Loading %s", url)
            await page.goto(url, wait_until="domcontentloaded", timeout=PAGE_LOAD_TIMEOUT_MS)

            # Wait for calendar
            try:
                await page.wait_for_selector("div.ConsumerCalendar-month", timeout=PAGE_LOAD_TIMEOUT_MS)
            except PWTimeout:
                log.error(
                    "[recon] Calendar not found for '%s'. "
                    "Is this a valid Tock slug? Cloudflare may be blocking.",
                    slug,
                )
                return result

            month_els = await page.query_selector_all("div.ConsumerCalendar-month")
            log.info("[recon] Found %d calendar month(s)", len(month_els))

            for month_el in month_els:
                # ── Parse heading ────────────────────────────────────────────
                heading_el = await month_el.query_selector(
                    "div.ConsumerCalendar-monthHeading span.H1"
                )
                if not heading_el:
                    continue
                heading_text = (await heading_el.inner_text()).strip()  # e.g. "March 2026"
                parts = heading_text.split()
                if len(parts) == 2:
                    month_name, yr = parts[0], parts[1]
                elif len(parts) == 1:
                    month_name, yr = parts[0], year
                else:
                    continue

                # ── Available days ───────────────────────────────────────────
                day_btns = await month_el.query_selector_all(
                    "button.ConsumerCalendar-day.is-in-month.is-available"
                )
                available_days: List[str] = []
                first_btn = None
                for btn in day_btns:
                    span = await btn.query_selector("span.B2")
                    if span:
                        txt = (await span.inner_text()).strip().zfill(2)
                        available_days.append(txt)
                        if first_btn is None:
                            first_btn = btn

                if not available_days:
                    log.info("[recon] %s %s: no available days", month_name, yr)
                    continue

                log.info(
                    "[recon] %s %s: %d available day(s): %s",
                    month_name, yr, len(available_days), available_days,
                )

                # ── Sample time slots by clicking the first available day ────
                time_slots: List[str] = []
                if first_btn:
                    try:
                        await first_btn.click()
                        await page.wait_for_selector(
                            "button.Consumer-resultsListItem.is-available",
                            timeout=8_000,
                        )
                        slot_btns = await page.query_selector_all(
                            "button.Consumer-resultsListItem.is-available"
                        )
                        for slot in slot_btns:
                            ts = await slot.query_selector(
                                "span.Consumer-resultsListItemTime span"
                            )
                            if ts:
                                time_slots.append((await ts.inner_text()).strip())
                        log.info("[recon] %s: time slots found: %s", month_name, time_slots)
                    except PWTimeout:
                        log.debug("[recon] No time slots loaded for %s", month_name)
                    except Exception as e:
                        log.debug("[recon] Error fetching time slots: %s", e)

                result[month_name] = {
                    "year":       yr,
                    "days":       available_days,
                    "time_slots": time_slots,
                }

        finally:
            await browser.close()

    return result


# ── Config builder ────────────────────────────────────────────────────────────

def _parse_time(t: str) -> Optional[datetime]:
    try:
        return datetime.strptime(t, RESERVATION_TIME_FORMAT)
    except (ValueError, TypeError):
        return None


def _time_str(dt: datetime) -> str:
    """Format datetime as '5:00 PM' (no leading zero on hour)."""
    # %-I is Linux/Mac; %#I is Windows
    fmt = "%-I:%M %p"
    try:
        return dt.strftime(fmt)
    except ValueError:
        return dt.strftime("%I:%M %p").lstrip("0")


def _build_tasks(slug: str, size: str, availability: Dict[str, dict]) -> List[Task]:
    tasks = []
    for month_name, data in availability.items():
        if not data["days"]:
            continue

        slots = [_parse_time(t) for t in data.get("time_slots", []) if _parse_time(t)]
        if slots:
            earliest_time = _time_str(min(slots))
            latest_time   = _time_str(max(slots))
        else:
            log.warning(
                "[recon] %s: could not sample time slots — using broad fallback window. "
                "Confirm actual availability and set times manually if needed.",
                month_name,
            )
            earliest_time = "11:00 AM"
            latest_time   = "11:30 PM"

        tasks.append(Task(
            url=slug,
            size=size,
            year=data["year"],
            month=month_name,
            days=data["days"],
            earliest_time=earliest_time,
            latest_time=latest_time,
        ))
    return tasks


# ── Optional Claude enhancement ───────────────────────────────────────────────

def _enhance_with_claude(slug: str, size: str, raw: Dict[str, dict]) -> Optional[List[Task]]:
    """
    If ANTHROPIC_API_KEY is set, ask Claude to review and refine the scraped
    availability data (e.g., better time-window selection).
    Falls back gracefully if the API is unavailable.
    """
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        return None

    try:
        import anthropic  # type: ignore
        client = anthropic.Anthropic(api_key=api_key)

        prompt = f"""
You are helping configure a reservation sniper for the Tock platform.

Restaurant slug : {slug}
Party size      : {size}
Raw scraped data: {json.dumps(raw, indent=2)}

Based on this data, return a JSON array of task objects with this schema:
[
  {{
    "url":           "{slug}",
    "size":          "{size}",
    "year":          "2026",
    "month":         "March",
    "days":          ["01", "15"],
    "earliest_time": "5:00 PM",
    "latest_time":   "9:30 PM"
  }}
]

Rules:
- Only include months/days that appear in the raw data.
- Set earliest_time to the first available slot.
- Set latest_time to the last available slot.
- Return ONLY the JSON array, no explanation.
"""
        resp = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=1024,
            messages=[{"role": "user", "content": prompt}],
        )
        text = resp.content[0].text.strip()
        if text.startswith("```"):
            text = text.split("```")[1].lstrip("json").strip()
        data = json.loads(text)
        return [Task.from_dict(d) for d in data]
    except Exception as e:
        log.debug("Claude enhancement failed (%s), using raw scrape", e)
        return None


# ── Public API ────────────────────────────────────────────────────────────────

async def recon(slug: str, size: str = DEFAULT_PARTY_SIZE) -> List[Task]:
    """
    Visit the Tock page for *slug* and return a list of Task configs
    representing all available dates within the acceptable time window.
    """
    log.info("[recon] Starting for %s (party of %s)", slug, size)
    availability = await _scrape_restaurant(slug, size)

    if not availability:
        log.warning("[recon] No availability found for %s", slug)
        return []

    # Try Claude enhancement first; fall back to pure scrape
    tasks = _enhance_with_claude(slug, size, availability)
    if tasks is None:
        tasks = _build_tasks(slug, size, availability)

    log.info("[recon] Generated %d task(s) for %s", len(tasks), slug)
    for t in tasks:
        log.info("  %s %s | days: %s | %s – %s", t.month, t.year, t.days, t.earliest_time, t.latest_time)

    return tasks


def save_config(tasks: List[Task], path: str) -> None:
    with open(path, "w") as f:
        json.dump([t.to_dict() for t in tasks], f, indent=2)
    log.info("[recon] Config saved → %s", path)


def load_config(path: str) -> List[Task]:
    with open(path) as f:
        data = json.load(f)
    return [Task.from_dict(d) for d in data]


# ── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")

    parser = argparse.ArgumentParser(description="Recon a Tock restaurant")
    parser.add_argument("restaurant", help="Tock slug, e.g. 'canlis'")
    parser.add_argument("--size", default="2")
    parser.add_argument("--save", metavar="FILE", help="Save config to JSON")
    args = parser.parse_args()

    tasks = asyncio.run(recon(args.restaurant, args.size))
    if args.save and tasks:
        save_config(tasks, args.save)
