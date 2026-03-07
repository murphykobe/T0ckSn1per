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
from typing import Optional
from playwright.async_api import async_playwright, Page, TimeoutError as PWTimeout

import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from recon import recon
from sniper import DayWorker, CHROME_EXECUTABLE, HEADLESS, USER_AGENT
from models import Task, Target
from tests.integration.conftest import TEST_SLUG, TEST_SIZE, tock_search_url

PAGE_LOAD_TIMEOUT  = 20_000  # ms
CART_WAIT_TIMEOUT  = 12_000  # ms — how long to wait for checkout page after Book click

# Cart detection signals — checked in order, first match wins.
# We prefer URL patterns and data-testid / visible text over CSS class names
# so these stay stable across Tock redesigns.
CART_SIGNALS = [
    # 1. URL navigates to the checkout flow (most reliable)
    ("url",     "**/checkout/**"),
    # 2. Tock puts a countdown hold timer on the checkout page
    ("testid",  "holding-time"),
    # 3. Checkout page heading (visible text, locale-independent enough)
    ("testid",  "reservation-date"),
    # 4. Step indicator present on the checkout page
    ("text",    "Complete your reservation"),
]


async def _apply_stealth(page: Page) -> None:
    try:
        from playwright_stealth import stealth_async  # type: ignore
        await stealth_async(page)
    except ImportError:
        await page.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
        )


async def _find_cart(page: Page) -> Optional[str]:
    """
    Wait for any cart/checkout signal to appear after the Book button is clicked.
    Returns a human-readable description of what matched, or None on timeout.
    """
    for kind, value in CART_SIGNALS:
        try:
            if kind == "url":
                await page.wait_for_url(value, timeout=CART_WAIT_TIMEOUT)
                return f"checkout URL: {page.url}"
            elif kind == "testid":
                await page.wait_for_selector(
                    f"[data-testid='{value}']", timeout=CART_WAIT_TIMEOUT
                )
                return f"data-testid={value!r}"
            elif kind == "text":
                await page.wait_for_selector(
                    f"text={value}", timeout=CART_WAIT_TIMEOUT
                )
                return f"visible text: {value!r}"
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

    # Use only the first task, first available target to keep the test fast
    task   = tasks[0]
    target = task.targets[0]

    print(f"\n[e2e] Target: {task.url} | {target.date} "
          f"| party {task.size} | {target.earliest_time}–{target.latest_time}")

    # ── Step 2: open a browser and poll until a slot is clicked ──────────────
    cart_selector_found = None
    winning_url         = None

    async with async_playwright() as p:
        _lkw: dict = {"headless": HEADLESS, "args": ["--disable-blink-features=AutomationControlled"]}
        if CHROME_EXECUTABLE:
            _lkw["executable_path"] = CHROME_EXECUTABLE
        browser = await p.chromium.launch(**_lkw)
        context = await browser.new_context(user_agent=USER_AGENT)
        page    = await context.new_page()
        await _apply_stealth(page)

        found_event = asyncio.Event()
        worker = DayWorker(
            task=task,
            target=target,
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
                f"Date {target.date} not available during polling window — "
                "availability may have disappeared between recon and snipe"
            )

        # ── Step 3: assert cart/checkout UI appeared ──────────────────────────
        winning_url   = page.url
        cart_signal   = await _find_cart(page)

        await browser.close()

    # ── Assertions ────────────────────────────────────────────────────────────
    print(f"[e2e] Page URL after click : {winning_url}")
    print(f"[e2e] Cart signal matched  : {cart_signal!r}")

    assert cart_signal is not None, (
        f"Book button was clicked for {task.url} on {target.date}, "
        f"but no checkout page appeared.\n"
        f"URL after click: {winning_url}\n"
        f"Signals tried: {CART_SIGNALS}\n"
    )

    print(f"\n[e2e] PASS — reservation added to cart: {cart_signal}")
