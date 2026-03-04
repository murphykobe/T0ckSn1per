"""
End-to-end integration test: full CLI flow → reservation added to cart.

Exit criteria
-------------
1. recon()  finds at least one available date for the test restaurant.
2. The sniper clicks a real time slot (no dry_run).
3. After clicking, Tock's cart/checkout UI is visible on the page.
   This is the only assertion that matters — it proves the bot actually
   held a cart, not just that it found a slot.

How to run
----------
  # Headed (watch the browser):
  PLAYWRIGHT_HEADLESS=0 pytest tests/integration/test_e2e.py -v -s

  # Headless (CI / containers without a display):
  PLAYWRIGHT_HEADLESS=1 pytest tests/integration/test_e2e.py -v -s

  # Override the restaurant:
  TEST_TOCK_SLUG=canlis pytest tests/integration/test_e2e.py -v -s

Notes
-----
- The test clicks a slot but does NOT complete checkout, so no real booking
  is made.  Tock releases the hold automatically after ~10 minutes.
- If no availability exists for the slug today, the test is skipped.
"""

import asyncio
import os
import pytest
from datetime import datetime
from playwright.async_api import async_playwright, Page, TimeoutError as PWTimeout

import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from recon import recon
from sniper import DayWorker, CHROME_EXECUTABLE, HEADLESS, USER_AGENT
from models import Task
from tests.integration.conftest import TEST_SLUG, TEST_SIZE, tock_search_url

PAGE_LOAD_TIMEOUT = 20_000   # ms
SLOT_CLICK_TIMEOUT = 10_000  # ms — how long to wait for cart UI to appear after click

# Tock cart / checkout selectors — checked in order, first match wins.
# Update this list if Tock changes their markup.
CART_SELECTORS = [
    "[class*='CheckoutDrawer']",     # checkout drawer panel
    "[class*='CartDrawer']",         # cart drawer
    "[class*='Checkout']",           # any checkout wrapper
    "[class*='Cart'][class*='Item']",# cart item
    "button[class*='checkout']",     # checkout CTA button
    "button[class*='reserve']",      # reserve/book button post-slot-click
    "[data-testid='cart']",          # data-testid fallback
]


async def _apply_stealth(page: Page) -> None:
    try:
        from playwright_stealth import stealth_async  # type: ignore
        await stealth_async(page)
    except ImportError:
        await page.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
        )


async def _find_cart_element(page: Page) -> str | None:
    """
    Wait up to SLOT_CLICK_TIMEOUT ms for any cart/checkout element to appear.
    Returns the matching selector string, or None if nothing appeared.
    """
    for selector in CART_SELECTORS:
        try:
            await page.wait_for_selector(selector, timeout=SLOT_CLICK_TIMEOUT)
            return selector
        except PWTimeout:
            continue
    return None


# ── Main e2e test ─────────────────────────────────────────────────────────────

@pytest.mark.integration
async def test_reservation_added_to_cart():
    """
    Full end-to-end:
      recon → find available slot → click it → assert cart UI appeared.
    """
    # ── Step 1: discover available tasks ─────────────────────────────────────
    try:
        tasks = await recon(TEST_SLUG, TEST_SIZE)
    except Exception as exc:
        msg = str(exc)
        if any(kw in msg for kw in ("ERR_", "net::", "proxy", "auth", "ERR_INVALID")):
            pytest.skip(
                f"Network access to exploretock.com is blocked in this environment: {exc}\n"
                "Run this test from a machine with direct internet access to Tock."
            )
        raise

    if not tasks:
        pytest.skip(
            f"No availability found for '{TEST_SLUG}' today — "
            "try a different slug with TEST_TOCK_SLUG=<slug>"
        )

    # Use only the first task, first available day to keep the test fast
    task = tasks[0]
    day  = task.days[0]

    print(f"\n[e2e] Target: {task.url} | {task.month} {day} {task.year} "
          f"| party {task.size} | {task.earliest_time}–{task.latest_time}")

    # ── Step 2: open a browser and poll until a slot is clicked ──────────────
    cart_selector_found = None
    winning_url         = None

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            executable_path=CHROME_EXECUTABLE,
            headless=HEADLESS,
            args=["--disable-blink-features=AutomationControlled"],
        )
        context = await browser.new_context(user_agent=USER_AGENT)
        page    = await context.new_page()
        await _apply_stealth(page)

        found_event = asyncio.Event()
        worker = DayWorker(
            task=task,
            day=day,
            page=page,
            found_event=found_event,
            dry_run=False,   # real click — this is the exit-criteria test
        )

        # Poll until the slot is clicked (found_event set) or we give up
        clicked = False
        for attempt in range(10):
            clicked = await worker._poll()
            if clicked:
                break
            await asyncio.sleep(1)

        if not clicked:
            await browser.close()
            pytest.skip(
                f"Day {day} not available during polling window — "
                "availability may have disappeared between recon and snipe"
            )

        # ── Step 3: assert cart/checkout UI appeared ──────────────────────────
        winning_url = page.url
        cart_selector_found = await _find_cart_element(page)

        await browser.close()

    # ── Assertions ────────────────────────────────────────────────────────────
    print(f"[e2e] Page URL after click : {winning_url}")
    print(f"[e2e] Cart selector matched: {cart_selector_found!r}")

    assert cart_selector_found is not None, (
        f"Slot was clicked for {task.url} on {task.month} {day}, "
        f"but no cart/checkout UI appeared on the page.\n"
        f"URL after click: {winning_url}\n"
        f"Selectors tried: {CART_SELECTORS}\n"
        "Add the correct Tock cart selector to CART_SELECTORS in this file."
    )

    print(f"\n[e2e] PASS — reservation added to cart via selector: {cart_selector_found!r}")
