# T0ckSn1per MVP Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Evolve the working Playwright-based sniper prototype into a complete MVP CLI matching the PRD — multi-target config, release mode, restock polling, auth via cookie file or interactive login, structured JSON output, and post-cart credential prompt.

**Architecture:** `models.py` defines `Target` (one date + time window) and `Task` (restaurant + list of targets). `sniper.py` runs one `DayWorker` per `Target` concurrently, all sharing a single browser context. `main.py` is the CLI entry point. All logs go to stderr; machine-readable output goes to stdout.

**Tech Stack:** Python 3.9+, Playwright (async), playwright-stealth, tomli (TOML config), argparse, asyncio, getpass (stdlib)

---

## Dependency Order

```
Task 1 (models)
  └─► Task 2 (DayWorker refactor)
        └─► Task 3 (CLI flags)
              ├─► Task 4 (auth)
              ├─► Task 5 (release mode)
              ├─► Task 6 (structured output)
              └─► Task 7 (post-cart prompt)
```

---

## Task 1: Refactor `models.py` — introduce `Target` dataclass

**Files:**
- Modify: `models.py`
- Modify: `tests/test_models.py`

**Context:** Currently `Task` stores `year`, `month`, `days` (list), `earliest_time`, `latest_time`. The PRD wants each target to have its own date + time window. We introduce `Target` to hold one date + time window; `Task` becomes restaurant + party size + list of `Target`s.

**Step 1: Write the failing tests**

```python
# tests/test_models.py — replace existing content with:
from models import Target, Task, RESERVATION_TIME_FORMAT
from datetime import datetime

def test_target_date_parse():
    t = Target(date="2026-03-15", earliest_time="5:00 PM", latest_time="9:30 PM")
    assert t.date == "2026-03-15"
    assert t.formatted_earliest() == datetime.strptime("5:00 PM", RESERVATION_TIME_FORMAT)
    assert t.formatted_latest()   == datetime.strptime("9:30 PM", RESERVATION_TIME_FORMAT)

def test_target_search_url():
    t = Target(date="2026-03-15", earliest_time="5:00 PM", latest_time="9:30 PM")
    url = t.search_url("alinea", "2")
    assert "exploretock.com/alinea/search" in url
    assert "date=2026-03-15" in url
    assert "size=2" in url

def test_task_from_dict_roundtrip():
    data = {
        "url": "alinea",
        "size": "2",
        "targets": [
            {"date": "2026-03-15", "earliest_time": "5:00 PM", "latest_time": "9:30 PM"},
            {"date": "2026-04-01", "earliest_time": "6:00 PM", "latest_time": "10:00 PM"},
        ],
    }
    task = Task.from_dict(data)
    assert task.url == "alinea"
    assert len(task.targets) == 2
    assert task.targets[0].date == "2026-03-15"
    assert task.to_dict() == data
```

**Step 2: Run test to verify it fails**

```bash
venv/bin/python3 -m pytest tests/test_models.py -v
```
Expected: ImportError or AttributeError — `Target` not defined yet.

**Step 3: Rewrite `models.py`**

```python
"""Shared data models."""

import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import List

RESERVATION_TIME_FORMAT = "%I:%M %p"


@dataclass
class Target:
    """One reservation target: a specific date + acceptable time window."""
    date:           str   # YYYY-MM-DD
    earliest_time:  str   # e.g. "5:00 PM"
    latest_time:    str   # e.g. "9:30 PM"

    def formatted_earliest(self) -> datetime:
        return datetime.strptime(self.earliest_time, RESERVATION_TIME_FORMAT)

    def formatted_latest(self) -> datetime:
        return datetime.strptime(self.latest_time, RESERVATION_TIME_FORMAT)

    def search_url(self, restaurant_slug: str, party_size: str) -> str:
        return (
            f"https://www.exploretock.com/{restaurant_slug}/search"
            f"?date={self.date}&size={party_size}&time=19%3A00"
        )

    def to_dict(self) -> dict:
        return {
            "date":           self.date,
            "earliest_time":  self.earliest_time,
            "latest_time":    self.latest_time,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Target":
        return cls(
            date=d["date"],
            earliest_time=d["earliest_time"],
            latest_time=d["latest_time"],
        )


@dataclass
class Task:
    """A restaurant reservation search task: one restaurant + N targets."""
    url:     str           # Tock slug, e.g. "alinea"
    size:    str           # party size, e.g. "2"
    targets: List[Target] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "url":     self.url,
            "size":    self.size,
            "targets": [t.to_dict() for t in self.targets],
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Task":
        return cls(
            url=d["url"],
            size=d["size"],
            targets=[Target.from_dict(t) for t in d.get("targets", [])],
        )

    def __repr__(self) -> str:
        return json.dumps(self.to_dict(), indent=2)
```

**Step 4: Run tests**

```bash
venv/bin/python3 -m pytest tests/test_models.py -v
```
Expected: All 3 tests PASS.

**Step 5: Commit**

```bash
git add models.py tests/test_models.py
git commit -m "refactor: introduce Target dataclass, Task now holds list of Targets"
```

---

## Task 2: Refactor `sniper.py` — `DayWorker` takes `Target`

**Files:**
- Modify: `sniper.py`
- Modify: `tests/test_sniper.py`

**Context:** `DayWorker` currently takes `task: Task` + `day: str` and builds the date from month/year/day. Now it takes `task: Task` + `target: Target`. The search URL comes from `target.search_url(task.url, task.size)`. Cart detection URL is also returned and stored.

**Step 1: Write failing unit tests**

```python
# tests/test_sniper.py — add/replace with:
import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from models import Task, Target
from sniper import DayWorker

@pytest.fixture
def task():
    return Task(url="alinea", size="2", targets=[
        Target(date="2026-03-15", earliest_time="5:00 PM", latest_time="9:30 PM"),
    ])

@pytest.fixture
def target(task):
    return task.targets[0]

@pytest.mark.asyncio
async def test_day_worker_accepts_target(task, target):
    """DayWorker can be constructed with Task + Target."""
    page = MagicMock()
    event = asyncio.Event()
    worker = DayWorker(task=task, target=target, page=page, found_event=event)
    assert worker.target.date == "2026-03-15"

@pytest.mark.asyncio
async def test_day_worker_try_day_uses_target_date(task, target):
    """_try_day looks for aria-label matching target.date."""
    page = AsyncMock()
    page.query_selector = AsyncMock(return_value=None)
    event = asyncio.Event()
    worker = DayWorker(task=task, target=target, page=page, found_event=event)
    result = await worker._try_day()
    assert result is False
    page.query_selector.assert_called_once()
    call_args = page.query_selector.call_args[0][0]
    assert "2026-03-15" in call_args
```

**Step 2: Run tests to verify they fail**

```bash
venv/bin/python3 -m pytest tests/test_sniper.py -v
```
Expected: ImportError or TypeError — DayWorker still expects old signature.

**Step 3: Update `sniper.py` — replace DayWorker and snipe_task**

Remove the old `MONTH_NUM` import (no longer needed). Update `DayWorker.__init__` and `_poll`, `_try_day`. Also track `checkout_url` on the event so callers can read it.

Key changes to `sniper.py`:

```python
# Remove from imports: MONTH_NUM
from models import Task, Target, RESERVATION_TIME_FORMAT

class DayWorker:
    def __init__(
        self,
        task: Task,
        target: Target,          # <-- replaces (task, day: str)
        page: Page,
        found_event: asyncio.Event,
        dry_run: bool = False,
    ):
        self.task        = task
        self.target      = target
        self.page        = page
        self.found_event = found_event
        self.dry_run     = dry_run
        self.checkout_url: Optional[str] = None  # set on success

    async def _poll(self) -> bool:
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
            log.debug("[%s/%s] Page timeout", self.task.url, self.target.date)
            return False
        except Exception as e:
            log.warning("[%s/%s] Load error: %s", self.task.url, self.target.date, e)
            return False

        await self.page.wait_for_timeout(2000)
        return await self._try_day()

    async def _try_day(self) -> bool:
        try:
            btn = await self.page.query_selector(
                f"button[data-testid='consumer-calendar-day']"
                f"[aria-label='{self.target.date}'][aria-disabled='false']"
            )
            if btn:
                log.info("[%s/%s] Day available — clicking", self.task.url, self.target.date)
                await btn.click()
                return await self._try_time()
            log.debug("[%s/%s] Day not available", self.task.url, self.target.date)
        except Exception as e:
            log.debug("[%s/%s] Error in _try_day: %s", self.task.url, self.target.date, e)
        return False

    async def _try_time(self) -> bool:
        try:
            await self.page.wait_for_selector(
                "[data-testid='search-result']", timeout=SLOT_WAIT_TIMEOUT
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
                if self.target.formatted_earliest() <= t <= self.target.formatted_latest():
                    book_btn = await card.query_selector("button[data-testid='booking-card-button']")
                    if not book_btn:
                        continue
                    log.info("[%s/%s] Clicking slot %s", self.task.url, self.target.date, time_text)
                    if not self.dry_run:
                        await book_btn.click()
                        # Wait briefly for navigation to checkout
                        try:
                            await self.page.wait_for_url("**/checkout/**", timeout=8000)
                            self.checkout_url = self.page.url
                        except PWTimeout:
                            self.checkout_url = self.page.url
                    return True
        except PWTimeout:
            log.debug("[%s/%s] No search-result cards", self.task.url, self.target.date)
        except Exception as e:
            log.debug("[%s/%s] Error in _try_time: %s", self.task.url, self.target.date, e)
        return False
```

Also update `snipe_task` to iterate `task.targets` instead of `task.days`:

```python
async def snipe_task(task: Task, dry_run: bool = False) -> Optional[dict]:
    """
    Returns a result dict on success: {restaurant, date, time, checkout_url}
    Returns None if no slot found.
    """
    found_event = asyncio.Event()
    winning_worker: Optional[DayWorker] = None

    async with async_playwright() as p:
        _launch_kwargs: dict = {
            "headless": HEADLESS,
            "args": ["--disable-blink-features=AutomationControlled"],
        }
        if CHROME_EXECUTABLE:
            _launch_kwargs["executable_path"] = CHROME_EXECUTABLE
        browser: Browser = await p.chromium.launch(**_launch_kwargs)
        context: BrowserContext = await browser.new_context(user_agent=USER_AGENT)

        workers: List[DayWorker] = []
        for i, target in enumerate(task.targets):
            page = await context.new_page()
            await _apply_stealth(page)
            workers.append(DayWorker(task, target, page, found_event, dry_run=dry_run))
            if i < len(task.targets) - 1:
                await asyncio.sleep(LAUNCH_STAGGER_SEC)

        await asyncio.gather(*[w.run() for w in workers], return_exceptions=True)

        # Find the winning worker
        for w in workers:
            if w.checkout_url is not None:
                winning_worker = w
                break

        await browser.close()

    if winning_worker:
        return {
            "restaurant":   task.url,
            "date":         winning_worker.target.date,
            "checkout_url": winning_worker.checkout_url or "",
        }
    return None
```

**Step 4: Run all tests**

```bash
venv/bin/python3 -m pytest tests/test_sniper.py tests/test_models.py -v
```
Expected: All tests PASS.

**Step 5: Fix `recon.py` — update to return `Task` with `targets`**

`recon.py` currently builds `Task` with the old fields. Update it to return `Task(url=..., size=..., targets=[Target(date=..., earliest_time=..., latest_time=...)])`.

Check `recon.py` and update the section that builds tasks. The `recon()` function should group discovered slots into `Target` objects with sane defaults for `earliest_time`/`latest_time` (e.g., `"12:00 PM"` to `"11:00 PM"` to catch anything).

**Step 6: Run smoke test to verify recon still works**

```bash
venv/bin/python3 -m pytest tests/test_recon.py -v
```

**Step 7: Commit**

```bash
git add sniper.py recon.py tests/test_sniper.py
git commit -m "refactor: DayWorker takes Target, snipe_task returns result dict"
```

---

## Task 3: New CLI flags in `main.py`

**Files:**
- Modify: `main.py`

**Context:** Add `--target`, `--interval`, `--max-duration`, `--release-at`, `--timezone`, `--cookies-file`, `--login`, `--prompt-login`, `--json` flags to the `snipe` and `run` subcommands.

**Step 1: No new unit tests needed** — CLI flag parsing is tested via functional use. Verify manually after.

**Step 2: Update `_build_parser()` in `main.py`**

Add a new `snipe` flag group and update `run` to accept inline targets:

```python
def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="t0cksn1per",
        description="Concurrent Tock reservation sniper",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # ── recon ──────────────────────────────────────────────────────────────
    p_recon = sub.add_parser("recon", help="Discover available dates for a restaurant")
    p_recon.add_argument("slug", help="Tock restaurant slug, e.g. 'canlis'")
    p_recon.add_argument("--size", default="2", help="Party size (default: 2)")
    p_recon.add_argument("--save", metavar="FILE", help="Save discovered config to JSON")

    # ── snipe ──────────────────────────────────────────────────────────────
    p_snipe = sub.add_parser("snipe", help="Snipe using a pre-built config file or inline targets")
    p_snipe.add_argument("--config", metavar="FILE", help="JSON config file from recon")
    p_snipe.add_argument(
        "--target", action="append", nargs=4,
        metavar=("DATE", "EARLIEST", "LATEST", "PARTY_SIZE"),
        help="Inline target: DATE=YYYY-MM-DD EARLIEST='5:00 PM' LATEST='9:30 PM' SIZE=2 (repeatable)",
    )
    p_snipe.add_argument("slug", nargs="?", help="Restaurant slug (required with --target)")
    p_snipe.add_argument("--dry-run", action="store_true", help="Find slots but don't click")
    p_snipe.add_argument("--interval", type=float, default=30.0, metavar="SECONDS",
                         help="Poll interval in seconds (default: 30)")
    p_snipe.add_argument("--max-duration", type=float, default=0, metavar="MINUTES",
                         help="Stop after N minutes with no success (default: unlimited)")
    p_snipe.add_argument("--release-at", metavar="HH:MM",
                         help="Fire all workers simultaneously at this local time (24h format)")
    p_snipe.add_argument("--timezone", default=None, metavar="TZ",
                         help="Timezone for --release-at, e.g. 'America/Chicago' (default: local)")
    p_snipe.add_argument("--cookies-file", metavar="FILE",
                         help="Netscape-format cookie file to load before polling")
    p_snipe.add_argument("--login", action="store_true",
                         help="Open Tock login page and wait for manual login before polling")
    p_snipe.add_argument("--prompt-login", action="store_true",
                         help="After cart add, prompt for Tock credentials via stdin")
    p_snipe.add_argument("--json", action="store_true",
                         help="Emit machine-readable JSON to stdout on exit")

    # ── run ────────────────────────────────────────────────────────────────
    p_run = sub.add_parser("run", help="Recon then snipe in one shot")
    p_run.add_argument("slug", help="Tock restaurant slug")
    p_run.add_argument("--size", default="2", help="Party size (default: 2)")
    p_run.add_argument("--dry-run", action="store_true")
    p_run.add_argument("--save", metavar="FILE", help="Also save recon config to JSON")
    p_run.add_argument("--interval", type=float, default=30.0, metavar="SECONDS")
    p_run.add_argument("--json", action="store_true")

    return parser
```

**Step 3: Update `_cmd_snipe` to build `Task` from inline `--target` flags**

```python
async def _cmd_snipe(args: argparse.Namespace) -> None:
    from models import Task, Target

    if args.config:
        tasks = load_config(args.config)
    elif args.target:
        if not args.slug:
            log.error("--target requires a restaurant slug as positional argument")
            sys.exit(2)
        targets = [
            Target(date=t[0], earliest_time=t[1], latest_time=t[2])
            for t in args.target
        ]
        tasks = [Task(url=args.slug, size=args.target[0][3], targets=targets)]
    else:
        log.error("Provide --config FILE or --target DATE EARLIEST LATEST SIZE")
        sys.exit(2)

    if not tasks:
        log.error("No tasks to run.")
        sys.exit(1)

    snipe_kwargs = {
        "dry_run":      args.dry_run,
        "interval":     args.interval,
        "max_duration": args.max_duration,
        "release_at":   getattr(args, "release_at", None),
        "cookies_file": getattr(args, "cookies_file", None),
        "interactive_login": getattr(args, "login", False),
        "prompt_login": getattr(args, "prompt_login", False),
    }

    results = await snipe_all(tasks, **snipe_kwargs)
    _print_results(results, json_mode=args.json)
```

**Step 4: Add `_print_results` helper**

```python
def _print_results(results: list, json_mode: bool) -> None:
    import json as _json
    any_success = any(r.get("status") == "success" for r in results)
    if json_mode:
        print(_json.dumps(results if len(results) > 1 else results[0]))
    else:
        for r in results:
            if r["status"] == "success":
                print(
                    f"\n{'='*60}\n"
                    f"  SLOT SECURED — complete checkout in your browser!\n"
                    f"  Restaurant : {r['restaurant']}\n"
                    f"  Date       : {r['date']}\n"
                    f"  Checkout   : {r.get('checkout_url', 'N/A')}\n"
                    f"{'='*60}\n",
                    file=sys.stdout,
                )
            else:
                print(f"[{r['restaurant']}] No slot found.", file=sys.stderr)
    sys.exit(0 if any_success else 1)
```

**Step 5: Redirect all `log` handlers to stderr only**

```python
# In main() setup block — remove FileHandler from stdout, log everything to stderr:
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)-12s] %(levelname)s: %(message)s",
    handlers=[
        logging.StreamHandler(sys.stderr),
        logging.FileHandler("t0cksn1per.log"),
    ],
)
```

**Step 6: Verify CLI help output**

```bash
venv/bin/python3 main.py snipe --help
```
Expected: All new flags listed.

**Step 7: Commit**

```bash
git add main.py
git commit -m "feat: add --target, --interval, --release-at, --cookies-file, --json CLI flags"
```

---

## Task 4: Auth — cookie file loading + interactive login

**Files:**
- Modify: `sniper.py`

**Context:** `snipe_task` needs to accept `cookies_file` and `interactive_login` params. Load a Netscape cookie file into the browser context before any navigation. For interactive login, open the login page and wait for the user.

**Step 1: Write failing test**

```python
# tests/test_sniper.py — add:
@pytest.mark.asyncio
async def test_parse_netscape_cookies():
    from sniper import _parse_netscape_cookies
    lines = [
        "# Netscape HTTP Cookie File\n",
        ".exploretock.com\tTRUE\t/\tTRUE\t0\t_tock_session\tabc123\n",
        "# comment\n",
        ".exploretock.com\tTRUE\t/\tFALSE\t0\ttock_user\txyz\n",
    ]
    cookies = _parse_netscape_cookies(lines)
    assert len(cookies) == 2
    assert cookies[0]["name"] == "_tock_session"
    assert cookies[0]["value"] == "abc123"
    assert cookies[0]["secure"] is True
    assert cookies[1]["name"] == "tock_user"
```

**Step 2: Run test to verify it fails**

```bash
venv/bin/python3 -m pytest tests/test_sniper.py::test_parse_netscape_cookies -v
```

**Step 3: Add `_parse_netscape_cookies` and cookie loading to `sniper.py`**

```python
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


async def _interactive_login(context: BrowserContext) -> None:
    """Open Tock login page and wait for the user to log in manually."""
    page = await context.new_page()
    await _apply_stealth(page)
    await page.goto("https://www.exploretock.com/login", timeout=PAGE_LOAD_TIMEOUT)
    log.info("Please log in to Tock in the browser window. Waiting up to 5 minutes...")
    print("\n[AUTH] Browser open — log in to Tock, then press Enter here to continue...",
          file=sys.stderr)
    # Wait for navigation away from /login (user completed login)
    try:
        await page.wait_for_url(
            lambda url: "/login" not in url, timeout=300_000
        )
        log.info("Login detected — continuing.")
    except PWTimeout:
        log.warning("Login timeout — continuing anyway.")
    await page.close()
```

Update `snipe_task` signature to accept these params:

```python
async def snipe_task(
    task: Task,
    dry_run: bool = False,
    interval: float = 30.0,
    max_duration: float = 0,
    release_at: Optional[str] = None,
    cookies_file: Optional[str] = None,
    interactive_login: bool = False,
    prompt_login: bool = False,
) -> Optional[dict]:
    ...
    # After context is created, before workers start:
    if cookies_file:
        await _load_cookies(context, cookies_file)
    if interactive_login:
        await _interactive_login(context)
    ...
```

Also add `import sys` at top of `sniper.py` if not already present.

**Step 4: Run tests**

```bash
venv/bin/python3 -m pytest tests/test_sniper.py -v
```

**Step 5: Commit**

```bash
git add sniper.py tests/test_sniper.py
git commit -m "feat: add cookie file loading and interactive login support"
```

---

## Task 5: Restock polling interval + max-duration + release mode

**Files:**
- Modify: `sniper.py`

**Context:** Currently `DayWorker.run()` loops with `REFRESH_DELAY_SEC`. Replace with the `interval` param passed through. Add `max_duration` to stop the whole run after N minutes. Add `release_at` scheduler: sleep until `release_at - 30s`, pre-load the page, then fire.

**Step 1: No new unit tests** — this is timing logic; verify by running the bot with `--dry-run`.

**Step 2: Update `DayWorker` to accept and use `interval`**

```python
class DayWorker:
    def __init__(self, task, target, page, found_event, dry_run=False, interval=30.0):
        ...
        self.interval = interval

    async def run(self) -> None:
        while not self.found_event.is_set():
            delay = self.interval + random.uniform(-min(self.interval * 0.1, 2), min(self.interval * 0.1, 2))
            await asyncio.sleep(max(0.5, delay))
            if await self._poll():
                self.found_event.set()
                ...
                break
```

**Step 3: Add `max_duration` cancellation to `snipe_task`**

```python
async def snipe_task(task, ..., max_duration=0.0, ...):
    found_event = asyncio.Event()
    ...
    tasks_coros = [w.run() for w in workers]
    if max_duration > 0:
        try:
            await asyncio.wait_for(
                asyncio.gather(*tasks_coros, return_exceptions=True),
                timeout=max_duration * 60,
            )
        except asyncio.TimeoutError:
            log.info("[%s] max-duration %gmin reached — stopping.", task.url, max_duration)
            found_event.set()  # signal workers to stop
    else:
        await asyncio.gather(*tasks_coros, return_exceptions=True)
```

**Step 4: Add `release_at` scheduler**

```python
import time
from datetime import datetime as dt

async def _wait_for_release(release_at: str, tz_name: Optional[str] = None) -> None:
    """Sleep until `release_at` HH:MM (local time). Pre-warms 30s before."""
    try:
        target_time = dt.strptime(release_at, "%H:%M").replace(
            year=dt.now().year, month=dt.now().month, day=dt.now().day
        )
    except ValueError:
        log.error("Invalid --release-at format. Use HH:MM (24h), e.g. '10:00'")
        return

    now = dt.now()
    if target_time < now:
        # Already past today — skip wait
        log.info("Release time %s already passed — firing immediately.", release_at)
        return

    pre_warm_at = target_time.timestamp() - 30
    sleep_until_prewarm = pre_warm_at - time.time()
    if sleep_until_prewarm > 0:
        log.info("Sleeping %.0fs until pre-warm (30s before %s)...", sleep_until_prewarm, release_at)
        await asyncio.sleep(sleep_until_prewarm)

    log.info("PRE-WARM: loading restaurant page...")
    # (Workers will navigate in their first _poll call — the sleep brings us to release time)
    remaining = target_time.timestamp() - time.time()
    if remaining > 0:
        log.info("Waiting %.1fs until release time %s...", remaining, release_at)
        await asyncio.sleep(remaining)
    log.info("RELEASE TIME — firing all workers!")
```

Call `await _wait_for_release(release_at)` in `snipe_task` before starting workers.

**Step 5: Verify release mode with a near-future time**

```bash
venv/bin/python3 main.py snipe alinea \
  --target 2026-03-15 "5:00 PM" "9:30 PM" 2 \
  --release-at $(date -v+1M +%H:%M) \
  --dry-run
```
Expected: Bot waits ~1 minute then fires, logging "RELEASE TIME — firing all workers!".

**Step 6: Commit**

```bash
git add sniper.py
git commit -m "feat: add polling interval, max-duration timeout, and release-at scheduler"
```

---

## Task 6: Structured output and exit codes

**Files:**
- Modify: `sniper.py`, `main.py`

**Context:** `snipe_task` currently returns `Optional[dict]`. Standardize the result to always return a dict with `status`, `restaurant`, `date`, `checkout_url`. `snipe_all` returns `List[dict]`. `main.py` `_print_results` emits JSON to stdout if `--json`, human text to stdout otherwise; all logs go to stderr.

**Step 1: Write failing test**

```python
# tests/test_sniper.py — add:
@pytest.mark.asyncio
async def test_snipe_all_returns_list_of_dicts():
    """snipe_all signature returns a list even with no tasks."""
    from sniper import snipe_all
    results = await snipe_all([])
    assert isinstance(results, list)
```

**Step 2: Update `snipe_all` in `sniper.py`**

```python
async def snipe_all(tasks: List[Task], **kwargs) -> List[dict]:
    results = []
    coros = [snipe_task(t, **kwargs) for t in tasks]
    raw = await asyncio.gather(*coros, return_exceptions=True)
    for task, outcome in zip(tasks, raw):
        if isinstance(outcome, Exception):
            results.append({"status": "error", "restaurant": task.url,
                            "error": str(outcome), "date": "", "checkout_url": ""})
        elif outcome:
            results.append({
                "status": "success",
                "restaurant": outcome["restaurant"],
                "date": outcome["date"],
                "checkout_url": outcome.get("checkout_url", ""),
            })
        else:
            results.append({"status": "no_slots", "restaurant": task.url,
                            "date": "", "checkout_url": ""})
    return results
```

**Step 3: Run tests**

```bash
venv/bin/python3 -m pytest tests/ -v --ignore=tests/integration
```
Expected: All pass.

**Step 4: Test JSON output manually**

```bash
venv/bin/python3 main.py snipe alinea \
  --target 2026-03-99 "5:00 PM" "9:30 PM" 2 \
  --dry-run --json 2>/dev/null
```
Expected: `{"status": "no_slots", ...}` printed to stdout.

**Step 5: Commit**

```bash
git add sniper.py main.py
git commit -m "feat: structured result dicts, exit codes 0/1/2, JSON output to stdout"
```

---

## Task 7: Post-cart credential prompt (`--prompt-login`)

**Files:**
- Modify: `sniper.py`

**Context:** After `_try_time` succeeds (cart add confirmed), if `prompt_login=True`, pause and collect email + password from stdin. Log in within the browser session. Print the checkout URL.

**Step 1: No unit test** — `getpass` reads from TTY which is not mockable in pytest. Test manually.

**Step 2: Add `_prompt_and_login` to `sniper.py`**

```python
import getpass

async def _prompt_and_login(page: Page, restaurant_slug: str) -> None:
    """After cart add: prompt for credentials, log in within the browser."""
    print(
        f"\n[AUTH] Slot secured! To tie this cart to your account:\n"
        f"  Enter your Tock credentials below (or Ctrl+C to skip).\n"
        f"  Alternatively, open exploretock.com/{restaurant_slug} in your browser\n"
        f"  and log in — the cart will be waiting.\n",
        file=sys.stderr,
    )
    try:
        email    = input("  Tock email: ")
        password = getpass.getpass("  Tock password: ")
    except (KeyboardInterrupt, EOFError):
        print("\n[AUTH] Skipped. Log in manually to complete checkout.", file=sys.stderr)
        return

    if not email or not password:
        print("[AUTH] No credentials entered — skipping login.", file=sys.stderr)
        return

    try:
        await page.goto("https://www.exploretock.com/login", timeout=PAGE_LOAD_TIMEOUT)
        await page.fill("input[name='email']",    email)
        await page.fill("input[name='password']", password)
        await page.click("button[type='submit']")
        await page.wait_for_url(
            lambda url: "/login" not in url, timeout=15_000
        )
        log.info("Login successful. Navigating back to checkout...")
        await page.goto(
            f"https://www.exploretock.com/{restaurant_slug}",
            timeout=PAGE_LOAD_TIMEOUT,
        )
        print(f"\n[AUTH] Logged in! Cart is ready at: exploretock.com/{restaurant_slug}",
              file=sys.stderr)
    except Exception as e:
        log.warning("Login failed: %s", e)
        print(f"[AUTH] Login failed ({e}). Log in manually.", file=sys.stderr)
```

**Step 3: Call `_prompt_and_login` from `DayWorker.run()` on success**

In `DayWorker.run()`, after `found_event.set()`, if `self.prompt_login`:

```python
# In DayWorker.__init__:
self.prompt_login = prompt_login   # new param, default False

# In DayWorker.run() after found_event.set():
if self.prompt_login and not self.dry_run:
    await _prompt_and_login(self.page, self.task.url)
```

Pass `prompt_login` through `snipe_task` → `DayWorker`.

**Step 4: Test manually**

```bash
PLAYWRIGHT_HEADLESS=0 venv/bin/python3 main.py snipe alinea \
  --target 2026-03-15 "5:00 PM" "9:30 PM" 2 \
  --prompt-login
```
Expected: Bot runs, on cart add prints credential prompt, accepts input.

**Step 5: Commit**

```bash
git add sniper.py
git commit -m "feat: --prompt-login collects Tock credentials post-cart and logs in"
```

---

## Task 8: Update E2E test + run full test suite

**Files:**
- Modify: `tests/integration/test_e2e.py`, `tests/integration/conftest.py`

**Context:** The E2E test still uses the old `DayWorker(task, day, ...)` signature. Update it to the new `DayWorker(task, target, ...)` signature. Confirm all unit + integration tests pass.

**Step 1: Update `test_e2e.py` to use `Target`**

Replace:
```python
task = tasks[0]
day  = task.days[0]
worker = DayWorker(task=task, day=day, page=page, found_event=found_event, dry_run=False)
```
With:
```python
task   = tasks[0]
target = task.targets[0]
worker = DayWorker(task=task, target=target, page=page, found_event=found_event, dry_run=False)
```

Also update the print statement from `task.month {day} {task.year}` to `target.date`.

**Step 2: Run unit tests**

```bash
venv/bin/python3 -m pytest tests/ -v --ignore=tests/integration
```
Expected: All pass.

**Step 3: Run integration E2E against Alinea (headed)**

```bash
PLAYWRIGHT_HEADLESS=0 venv/bin/python3 -m pytest tests/integration/test_e2e.py -v -s
```
Expected: PASS — slot clicked, checkout URL confirmed.

**Step 4: Final commit**

```bash
git add tests/
git commit -m "test: update E2E test to new Target-based DayWorker API"
```

---

## Verification Checklist

Before calling this plan complete:

- [ ] `venv/bin/python3 -m pytest tests/ -v --ignore=tests/integration` — all unit tests PASS
- [ ] `venv/bin/python3 main.py snipe --help` — all new flags listed
- [ ] `venv/bin/python3 main.py snipe alinea --target 2026-03-99 "5:00 PM" "9:30 PM" 2 --dry-run --json` — exits 1, JSON on stdout, logs on stderr
- [ ] `PLAYWRIGHT_HEADLESS=0 venv/bin/python3 -m pytest tests/integration/test_e2e.py -v -s` — PASS (or SKIP if no Alinea availability)
- [ ] Update `tasks/prd-tock-reservation-sniper.md` acceptance criteria checkboxes as features land
