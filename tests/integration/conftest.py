"""
Shared fixtures for integration tests.

Environment variables
---------------------
PLAYWRIGHT_HEADLESS=1   Run headless (required in CI / containers without a display).
                        Default: 0 (headed — you can watch the browser work).
TEST_TOCK_SLUG          Tock restaurant slug.  Default: "alinea"
TEST_TOCK_SIZE          Party size string.     Default: "2"
CHROME_EXECUTABLE       Override the Chromium binary path.
"""

import os
import pytest
from datetime import datetime
from playwright.async_api import async_playwright, Browser, BrowserContext, Page

CHROME_EXECUTABLE = os.environ.get("CHROME_EXECUTABLE") or None
HEADLESS = os.environ.get("PLAYWRIGHT_HEADLESS", "0") == "1"

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

TEST_SLUG = os.environ.get("TEST_TOCK_SLUG", "alinea")
TEST_SIZE = os.environ.get("TEST_TOCK_SIZE", "2")


def tock_search_url(slug: str = TEST_SLUG, size: str = TEST_SIZE) -> str:
    now = datetime.now()
    return (
        f"https://www.exploretock.com/{slug}/search"
        f"?date={now.year}-{now.month:02d}-01&size={size}&time=19%3A00"
    )


@pytest.fixture
async def browser():
    """Launch a real Chromium browser; close it after the test."""
    async with async_playwright() as p:
        _lkw: dict = {"headless": HEADLESS, "args": ["--disable-blink-features=AutomationControlled"]}
        if CHROME_EXECUTABLE:
            _lkw["executable_path"] = CHROME_EXECUTABLE
        b: Browser = await p.chromium.launch(**_lkw)
        yield b
        await b.close()


@pytest.fixture
async def context(browser: Browser) -> BrowserContext:
    """Create a browser context with a realistic User-Agent."""
    ctx = await browser.new_context(user_agent=USER_AGENT)
    yield ctx
    await ctx.close()


@pytest.fixture
async def page(context: BrowserContext) -> Page:
    """Open a single page with stealth patches applied."""
    p = await context.new_page()
    try:
        from playwright_stealth import stealth_async  # type: ignore
        await stealth_async(p)
    except ImportError:
        await p.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
        )
    yield p
    await p.close()
