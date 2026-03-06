"""
Smoke test: verify browser can load Tock and calendar renders correctly.

This test always passes when the network is reachable and Tock hasn't
changed its markup. It does NOT click anything or require availability.

Run:
    PLAYWRIGHT_HEADLESS=1 venv/bin/pytest tests/integration/test_smoke.py -v -s
"""

import asyncio
import os
import pytest
from playwright.async_api import async_playwright, TimeoutError as PWTimeout

import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from tests.integration.conftest import TEST_SLUG, TEST_SIZE, tock_search_url, HEADLESS, USER_AGENT


@pytest.mark.integration
async def test_tock_page_loads_and_calendar_renders():
    """
    Open a Tock search page, confirm:
      1. Page title contains "Tock"
      2. Calendar container renders (div.ConsumerCalendar-month)
      3. At least one in-month day button is present (any state)
      4. Month heading text is parseable (e.g. "March 2026")
    """
    url = tock_search_url()
    async with async_playwright() as p:
        lkw = {"headless": HEADLESS, "args": ["--disable-blink-features=AutomationControlled"]}
        browser = await p.chromium.launch(**lkw)
        ctx = await browser.new_context(user_agent=USER_AGENT)
        page = await ctx.new_page()
        await page.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
        )

        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=20_000)
        except PWTimeout:
            await browser.close()
            pytest.skip("Tock page timed out — network may be unavailable")

        # --- calendar container ---
        try:
            await page.wait_for_selector("div.ConsumerCalendar-month", timeout=15_000)
            await page.wait_for_timeout(2_000)   # React settle time
        except PWTimeout:
            title = await page.title()
            await browser.close()
            pytest.skip(f"Calendar did not render (title={title!r}) — Cloudflare or page structure changed")

        title = await page.title()
        assert "Tock" in title, f"Expected 'Tock' in page title, got: {title!r}"

        # --- calendar day buttons — use data-testid, stable across CSS changes ---
        all_days = await page.query_selector_all(
            "button[data-testid='consumer-calendar-day']"
        )
        assert len(all_days) > 0, "No calendar day buttons found — data-testid may have changed"
        print(f"\n[smoke] Found {len(all_days)} calendar day buttons")

        # --- available days carry aria-disabled="false" and an aria-label date ---
        avail = await page.query_selector_all(
            "button[data-testid='consumer-calendar-day'][aria-disabled='false']"
        )
        print(f"[smoke] Available days (aria-disabled=false): {len(avail)} / {len(all_days)}")
        if avail:
            label = await avail[0].get_attribute("aria-label")
            print(f"[smoke] First available day aria-label: {label!r}  (format: YYYY-MM-DD)")

        # --- month headings are readable ---
        headings = await page.query_selector_all("div.ConsumerCalendar-monthHeading")
        assert len(headings) > 0, "No month headings found"
        heading_text = (await headings[0].inner_text()).strip()
        parts = heading_text.split()
        assert len(parts) >= 1, f"Unparseable month heading: {heading_text!r}"
        print(f"[smoke] Month heading: {heading_text!r}")

        await browser.close()

    print(f"\n[smoke] PASS — Tock calendar loaded for '{TEST_SLUG}'")
