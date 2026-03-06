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

from models import Task, Target, RESERVATION_TIME_FORMAT

log = logging.getLogger(__name__)

# ── Config ────────────────────────────────────────────────────────────────────

CHROME_EXECUTABLE = os.environ.get("CHROME_EXECUTABLE") or None
# Set PLAYWRIGHT_HEADLESS=1 in environments without a display (CI, containers)
HEADLESS             = os.environ.get("PLAYWRIGHT_HEADLESS", "0") == "1"
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
    Open the Tock search page for *slug* and extract available dates.
    Returns a dict keyed by ISO date string ("YYYY-MM-DD"):
        { "2026-03-15": { "time_slots": ["5:00 PM", ...] }, ... }
    """
    month_n = f"{datetime.now().month:02d}"
    year    = str(datetime.now().year)
    url     = (
        f"https://www.exploretock.com/{slug}/search"
        f"?date={year}-{month_n}-01&size={size}&time=19%3A00"
    )

    result: Dict[str, dict] = {}

    async with async_playwright() as p:
        _launch_kwargs: dict = {"headless": HEADLESS, "args": ["--disable-blink-features=AutomationControlled"]}
        if CHROME_EXECUTABLE:
            _launch_kwargs["executable_path"] = CHROME_EXECUTABLE
        browser: Browser = await p.chromium.launch(**_launch_kwargs)  # non-headless helps with Cloudflare Turnstile
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

            # Wait for calendar + extra settle time for React to render day buttons
            try:
                await page.wait_for_selector("div.ConsumerCalendar-month", timeout=PAGE_LOAD_TIMEOUT_MS)
                await page.wait_for_timeout(2000)
            except PWTimeout:
                log.error(
                    "[recon] Calendar not found for '%s'. "
                    "Is this a valid Tock slug? Cloudflare may be blocking.",
                    slug,
                )
                return result

            # ── Collect all available days via aria-label ("YYYY-MM-DD") ─────
            # Each available day button carries data-testid="consumer-calendar-day"
            # and aria-disabled="false".
            day_btns = await page.query_selector_all(
                "button[data-testid='consumer-calendar-day'][aria-disabled='false']"
            )
            log.info("[recon] Found %d available day button(s)", len(day_btns))

            # Group by date string; remember first button per date for time-slot sampling.
            from collections import defaultdict
            dates: Dict[str, dict] = {}   # date_str → {first_btn}
            for btn in day_btns:
                label = await btn.get_attribute("aria-label")  # "2026-03-05"
                if not label or len(label) != 10:
                    continue
                if label not in dates:
                    dates[label] = {"first_btn": btn}

            for date_str, data in dates.items():
                first_btn = data["first_btn"]
                log.info("[recon] Available date: %s", date_str)

                # ── Sample time slots by clicking the available day ──────────
                time_slots: List[str] = []
                try:
                    await first_btn.click()
                    await page.wait_for_selector(
                        "[data-testid='search-result']",
                        timeout=8_000,
                    )
                    cards = await page.query_selector_all("[data-testid='search-result']")
                    for card in cards:
                        time_el = await card.query_selector("[data-testid='search-result-time']")
                        if time_el:
                            time_slots.append((await time_el.inner_text()).strip())
                    log.info("[recon] %s: time slots found: %s", date_str, time_slots)
                except PWTimeout:
                    log.debug("[recon] No search-result cards loaded for %s", date_str)
                except Exception as e:
                    log.debug("[recon] Error fetching time slots: %s", e)

                result[date_str] = {"time_slots": time_slots}

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
    """
    Build one Task per restaurant, grouping all available dates as Targets.

    *availability* maps date_str ("YYYY-MM-DD") → {"time_slots": [...]}
    """
    if not availability:
        return []

    targets: List[Target] = []
    for date_str, data in availability.items():
        slots = [_parse_time(t) for t in data.get("time_slots", []) if _parse_time(t)]
        if slots:
            earliest_time = _time_str(min(slots))
            latest_time   = _time_str(max(slots))
        else:
            log.warning(
                "[recon] %s: could not sample time slots — using broad fallback window. "
                "Confirm actual availability and set times manually if needed.",
                date_str,
            )
            earliest_time = "12:00 PM"
            latest_time   = "11:00 PM"

        targets.append(Target(
            date=date_str,
            earliest_time=earliest_time,
            latest_time=latest_time,
        ))

    if not targets:
        return []

    return [Task(url=slug, size=size, targets=targets)]


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

Based on this data, return a JSON array containing exactly ONE task object with this schema:
[
  {{
    "url":     "{slug}",
    "size":    "{size}",
    "targets": [
      {{
        "date":           "2026-03-15",
        "earliest_time":  "5:00 PM",
        "latest_time":    "9:30 PM"
      }}
    ]
  }}
]

Rules:
- Include one Target per available date from the raw data.
- Set earliest_time to the first available slot for that date.
- Set latest_time to the last available slot for that date.
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
        log.info("  %s | %d target(s)", t.url, len(t.targets))
        for tgt in t.targets:
            log.info("    %s | %s – %s", tgt.date, tgt.earliest_time, tgt.latest_time)

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
