# Monitoring And Forward Window Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add forward-looking recon, persistent launch monitoring, explicit monitoring mode, and compact date-range input so the sniper can keep watching for newly released slots and expired-hold returns instead of exiting after one pass.

**Architecture:** Keep the existing CLI and worker engine, but expand the targeting layer so it can build dates from explicit lists, date ranges, or forward windows. Extend recon to scan multiple months ahead, then teach the runtime to keep refreshing eligible dates for a bounded monitoring duration in both launch and regular monitoring modes.

**Tech Stack:** Python 3.9+, argparse, asyncio, Playwright async API, dataclasses in `models.py`, pytest

---

## File Structure

- Modify: `main.py`
  Add CLI flags and routing helpers for monitoring mode, date ranges, and default forward windows.
- Modify: `models.py`
  Add helpers for expanding date ranges and handling empty-date selectors against eligible windows.
- Modify: `recon.py`
  Replace single-month scraping with forward-window scanning across multiple months.
- Modify: `sniper.py`
  Add persistent monitoring loops for launch and regular modes, plus better status logging.
- Modify: `README.md`
  Document launch monitoring, monitoring mode, date ranges, and default windows.
- Modify: `tests/test_main.py`
  Cover CLI routing, no-date defaults, and date-range parsing.
- Modify: `tests/test_models.py`
  Cover date-range expansion and forward-window selector behavior.
- Modify: `tests/test_recon.py`
  Cover forward-window date collection and task generation expectations.
- Modify: `tests/test_sniper.py`
  Cover persistent monitoring behavior and launch-mode retry semantics.

### Task 1: Add Date Range And Window Helpers

**Files:**
- Modify: `models.py`
- Test: `tests/test_models.py`

- [ ] **Step 1: Write the failing model tests**

Add to `tests/test_models.py`:

```python
from models import expand_date_ranges


def test_expand_date_ranges_supports_multiple_ranges():
    dates = expand_date_ranges("2026-05-07:2026-05-09,2026-05-21:2026-05-25")
    assert dates == [
        "2026-05-07",
        "2026-05-08",
        "2026-05-09",
        "2026-05-21",
        "2026-05-22",
        "2026-05-23",
        "2026-05-24",
        "2026-05-25",
    ]


def test_expand_date_ranges_returns_empty_for_blank_input():
    assert expand_date_ranges(None) == []
    assert expand_date_ranges("") == []
```

- [ ] **Step 2: Run the targeted tests to verify they fail**

Run: `venv/bin/pytest tests/test_models.py::test_expand_date_ranges_supports_multiple_ranges tests/test_models.py::test_expand_date_ranges_returns_empty_for_blank_input -v`
Expected: FAIL because `expand_date_ranges` does not exist.

- [ ] **Step 3: Add the minimal date-range helper**

In `models.py`, add:

```python
from datetime import datetime, timedelta


def expand_date_ranges(raw: Optional[str]) -> List[str]:
    if not raw:
        return []

    expanded: List[str] = []
    for part in [p.strip() for p in raw.split(",") if p.strip()]:
        start_s, end_s = [token.strip() for token in part.split(":", 1)]
        start = datetime.strptime(start_s, "%Y-%m-%d").date()
        end = datetime.strptime(end_s, "%Y-%m-%d").date()
        cursor = start
        while cursor <= end:
            expanded.append(cursor.isoformat())
            cursor += timedelta(days=1)
    return expanded
```

- [ ] **Step 4: Run the targeted tests to verify they pass**

Run: `venv/bin/pytest tests/test_models.py::test_expand_date_ranges_supports_multiple_ranges tests/test_models.py::test_expand_date_ranges_returns_empty_for_blank_input -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add models.py tests/test_models.py
git commit -m "feat: add date range expansion helpers"
```

### Task 2: Add CLI Surface For Monitoring And Date Ranges

**Files:**
- Modify: `main.py`
- Test: `tests/test_main.py`

- [ ] **Step 1: Write the failing parser and routing tests**

Add to `tests/test_main.py`:

```python
def test_parser_accepts_monitoring_flags_and_date_ranges():
    parser = _build_parser()

    args = parser.parse_args([
        "run",
        "taneda",
        "--size", "1",
        "--monitor",
        "--monitor-duration", "20",
        "--date-ranges", "2026-05-07:2026-05-09",
    ])

    assert args.monitor is True
    assert args.monitor_duration == 20
    assert args.date_ranges == "2026-05-07:2026-05-09"


def test_build_inline_task_expands_date_ranges():
    args = argparse.Namespace(
        slug="taneda",
        size="1",
        target=None,
        date=[],
        dates=None,
        date_ranges="2026-05-07:2026-05-09",
        exact_time=[],
        exact_times=None,
        release_at=None,
        newly_released_only=False,
        monitor=True,
        monitor_duration=15,
    )

    task = _build_inline_task(args)

    assert [selector.to_dict() for selector in task.selectors] == [
        {"dates": ["2026-05-07", "2026-05-08", "2026-05-09"]}
    ]
```

- [ ] **Step 2: Run the targeted tests to verify they fail**

Run: `venv/bin/pytest tests/test_main.py::test_parser_accepts_monitoring_flags_and_date_ranges tests/test_main.py::test_build_inline_task_expands_date_ranges -v`
Expected: FAIL because the parser does not know `--monitor`, `--monitor-duration`, or `--date-ranges`.

- [ ] **Step 3: Add the CLI flags and date collection logic**

In `main.py`, add these parser flags to both `snipe` and `run`:

```python
p_snipe.add_argument("--date-ranges",
                     help="Comma-separated date ranges, e.g. '2026-05-07:2026-05-09,2026-05-21:2026-05-25'")
p_snipe.add_argument("--monitor", action="store_true",
                     help="Keep polling for cancellations/restocks instead of exiting after one pass")
p_snipe.add_argument("--monitor-duration", type=float, default=15.0, metavar="MINUTES",
                     help="Monitoring duration in minutes (default: 15)")
```

Add the same three flags to `p_run`.

Update `_build_inline_task()` so it merges:

```python
from models import expand_date_ranges

dates = _collect_inline_values(args, "date", "dates")
dates.extend(expand_date_ranges(getattr(args, "date_ranges", None)))
```

- [ ] **Step 4: Update inline-task routing to treat monitoring as explicit runtime intent**

In `main.py`, extend `_should_use_inline_task()`:

```python
is_monitoring_without_date_preference = bool(getattr(args, "monitor", False))
return has_inline_dates or has_inline_exact_times or is_launch_without_date_preference or is_monitoring_without_date_preference
```

- [ ] **Step 5: Thread monitoring kwargs through to the runtime**

Update both `snipe_kwargs` blocks:

```python
monitor=getattr(args, "monitor", False),
monitor_duration=getattr(args, "monitor_duration", 15.0),
```

- [ ] **Step 6: Run the targeted tests to verify they pass**

Run: `venv/bin/pytest tests/test_main.py::test_parser_accepts_monitoring_flags_and_date_ranges tests/test_main.py::test_build_inline_task_expands_date_ranges -v`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add main.py tests/test_main.py
git commit -m "feat: add monitoring and date range CLI flags"
```

### Task 3: Teach Recon To Look Ahead Across Multiple Months

**Files:**
- Modify: `recon.py`
- Test: `tests/test_recon.py`

- [ ] **Step 1: Write the failing recon helper tests**

Add to `tests/test_recon.py`:

```python
from recon import _month_starts_for_lookahead


def test_month_starts_for_lookahead_spans_future_months():
    starts = _month_starts_for_lookahead("2026-04-18", lookahead_days=60)
    assert starts == ["2026-04-01", "2026-05-01", "2026-06-01"]
```

- [ ] **Step 2: Run the targeted test to verify it fails**

Run: `venv/bin/pytest tests/test_recon.py::test_month_starts_for_lookahead_spans_future_months -v`
Expected: FAIL because `_month_starts_for_lookahead` does not exist.

- [ ] **Step 3: Add month-window helpers**

In `recon.py`, add:

```python
from datetime import date, timedelta


def _month_starts_for_lookahead(today_iso: Optional[str] = None, lookahead_days: int = 60) -> List[str]:
    today = datetime.strptime(today_iso, "%Y-%m-%d").date() if today_iso else datetime.now().date()
    end = today + timedelta(days=lookahead_days)
    cursor = date(today.year, today.month, 1)
    starts: List[str] = []
    while cursor <= end:
        starts.append(cursor.isoformat())
        if cursor.month == 12:
            cursor = date(cursor.year + 1, 1, 1)
        else:
            cursor = date(cursor.year, cursor.month + 1, 1)
    return starts
```

- [ ] **Step 4: Replace single-month recon with multi-month scanning**

Refactor `_scrape_restaurant()` so it iterates the month starts returned by `_month_starts_for_lookahead()` and merges all discovered available dates into one result map:

```python
for month_start in _month_starts_for_lookahead(lookahead_days=lookahead_days):
    url = (
        f"https://www.exploretock.com/{slug}/search"
        f"?date={month_start}&size={size}&time=19%3A00"
    )
```

Only keep dates that are within the requested lookahead horizon.

- [ ] **Step 5: Add a public lookahead argument to recon**

Change the signature:

```python
async def recon(slug: str, size: str = DEFAULT_PARTY_SIZE, lookahead_days: int = 60) -> List[Task]:
```

Thread `lookahead_days` into `_scrape_restaurant()`.

- [ ] **Step 6: Run the targeted tests**

Run: `venv/bin/pytest tests/test_recon.py::test_month_starts_for_lookahead_spans_future_months -v`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add recon.py tests/test_recon.py
git commit -m "feat: make recon scan forward across months"
```

### Task 4: Add Persistent Monitoring In The Runtime

**Files:**
- Modify: `sniper.py`
- Test: `tests/test_sniper.py`

- [ ] **Step 1: Write the failing monitoring tests**

Add to `tests/test_sniper.py`:

```python
@pytest.mark.asyncio
async def test_launch_mode_does_not_exit_after_first_empty_new_release_diff():
    from sniper import _monitor_newly_released_dates

    snapshots = [
        {"2026-05-01"},
        {"2026-05-01"},
        {"2026-05-01", "2026-05-21"},
    ]

    result = await _monitor_newly_released_dates(
        before_dates=snapshots[0],
        poll_snapshots=snapshots[1:],
        requested_dates=["2026-05-21"],
    )

    assert result == ["2026-05-21"]
```

Use a small focused helper with injected snapshots rather than trying to unit-test full browser behavior directly.

- [ ] **Step 2: Run the targeted test to verify it fails**

Run: `venv/bin/pytest tests/test_sniper.py::test_launch_mode_does_not_exit_after_first_empty_new_release_diff -v`
Expected: FAIL because `_monitor_newly_released_dates` does not exist.

- [ ] **Step 3: Add a launch monitoring helper**

In `sniper.py`, add a helper that repeatedly polls available dates until one of these happens:

- newly released eligible dates are found
- monitor duration expires

Minimal shape:

```python
async def _monitor_newly_released_dates(
    page: Page,
    task: Task,
    requested_dates: Optional[list],
    interval: float,
    deadline: Optional[float],
) -> list:
    baseline = await _capture_available_dates(page, task.url, task.size)
    while True:
        current = await _capture_available_dates(page, task.url, task.size)
        eligible = _newly_released_dates(
            before=baseline,
            after=current,
            requested_dates=requested_dates,
        )
        if eligible:
            return eligible
        if deadline is not None and asyncio.get_running_loop().time() >= deadline:
            return []
        await asyncio.sleep(interval)
```

- [ ] **Step 4: Add explicit monitoring parameters to the runtime**

Update `snipe_task()` and `snipe_all()` signatures:

```python
async def snipe_task(..., monitor: bool = False, monitor_duration: float = 15.0, ...) -> Optional[dict]:
```

Use a deadline:

```python
deadline = asyncio.get_running_loop().time() + (monitor_duration * 60) if monitor or launch_newly_released_only else None
```

- [ ] **Step 5: Replace the one-shot launch diff with a monitoring loop**

In the `launch_newly_released_only` branch, replace the single `before/after` comparison with repeated polling until either:

- eligible dates appear
- the monitoring deadline expires

Do not `return None` after the first empty diff.

- [ ] **Step 6: Support regular monitoring mode**

When `monitor=True` and there are explicit or default target dates, keep worker tasks alive until:

- a slot is secured
- monitor duration expires

Do not special-case monitoring as a one-shot recon result.

- [ ] **Step 7: Run the targeted monitoring test**

Run: `venv/bin/pytest tests/test_sniper.py::test_launch_mode_does_not_exit_after_first_empty_new_release_diff -v`
Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add sniper.py tests/test_sniper.py
git commit -m "feat: add persistent launch and monitoring loops"
```

### Task 5: Wire Default Windows Into Run And Snipe

**Files:**
- Modify: `main.py`
- Modify: `recon.py`
- Test: `tests/test_main.py`

- [ ] **Step 1: Write the failing default-window tests**

Add to `tests/test_main.py`:

```python
def test_launch_mode_without_dates_uses_default_30_day_window():
    args = argparse.Namespace(
        slug="taneda",
        size="1",
        target=None,
        date=[],
        dates=None,
        date_ranges=None,
        exact_time=[],
        exact_times=None,
        release_at="11:00",
        newly_released_only=True,
        monitor=False,
        monitor_duration=15.0,
    )

    task = _build_inline_task(args)

    assert task.selectors == [Selector(dates=[])]
```

Keep the selector empty here and let the runtime expand it against the launch window.

- [ ] **Step 2: Run the targeted test to verify behavior**

Run: `venv/bin/pytest tests/test_main.py::test_launch_mode_without_dates_uses_default_30_day_window -v`
Expected: PASS or near-pass, confirming the CLI keeps empty selectors for runtime-driven expansion.

- [ ] **Step 3: Pass lookahead defaults into recon**

In `_cmd_run()`, change:

```python
tasks = await recon(args.slug, size=args.size, lookahead_days=60)
```

In `_cmd_recon()`, change:

```python
tasks = await recon(args.slug, size=args.size, lookahead_days=60)
```

- [ ] **Step 4: Add default-window helpers for runtime-driven no-date launch**

In `sniper.py`, when `launch_newly_released_only` is active and `_requested_dates_from_task(task)` is empty, compute requested dates from the next `30` days before filtering newly released dates.

Use a helper like:

```python
def _default_launch_window_dates(days: int = 30) -> list:
    today = datetime.now().date()
    return [(today + timedelta(days=offset)).isoformat() for offset in range(days)]
```

- [ ] **Step 5: Run the focused tests**

Run: `venv/bin/pytest tests/test_main.py tests/test_models.py tests/test_recon.py tests/test_sniper.py -q`
Expected: PASS for the targeted unit suites.

- [ ] **Step 6: Commit**

```bash
git add main.py recon.py sniper.py tests/test_main.py tests/test_models.py tests/test_recon.py tests/test_sniper.py
git commit -m "fix: add forward windows for launch and regular monitoring"
```

### Task 6: Update Documentation

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Document the new flags**

Add CLI docs for:

```md
--date-ranges "2026-05-07:2026-05-09,2026-05-21:2026-05-25"
--monitor
--monitor-duration 15
```

- [ ] **Step 2: Add launch monitoring examples**

Add examples:

```bash
PLAYWRIGHT_HEADLESS=0 venv/bin/python main.py run taneda \
  --size 1 \
  --release-at 11:00 \
  --newly-released-only \
  --exact-times "5:15 PM,7:45 PM"
```

```bash
PLAYWRIGHT_HEADLESS=0 venv/bin/python main.py run taneda \
  --size 1 \
  --monitor \
  --interval 5
```

- [ ] **Step 3: Clarify default windows**

Add text:

```md
- launch mode without dates targets the next 30 calendar days by default
- regular recon and monitoring look ahead 60 calendar days by default
```

- [ ] **Step 4: Verify docs are readable**

Run: `sed -n '1,260p' README.md`
Expected: the new examples and defaults are visible and consistent with the CLI.

- [ ] **Step 5: Commit**

```bash
git add README.md
git commit -m "docs: add monitoring and forward window examples"
```

### Task 7: Final Verification

**Files:**
- Modify: none

- [ ] **Step 1: Run the full non-integration suite**

Run: `venv/bin/pytest tests/ --ignore=tests/integration -q`
Expected: PASS.

- [ ] **Step 2: Run CLI help smoke checks**

Run: `venv/bin/python main.py run --help`
Expected: PASS and shows `--date-ranges`, `--monitor`, and `--monitor-duration`.

Run: `venv/bin/python main.py snipe --help`
Expected: PASS and shows the same new flags.

- [ ] **Step 3: Run a live smoke check outside the sandbox**

Run:

```bash
PLAYWRIGHT_HEADLESS=1 venv/bin/pytest tests/integration/test_smoke.py -q -s
```

Expected: PASS with a real Tock calendar rendering.

- [ ] **Step 4: Run a live no-date monitoring dry-run outside the sandbox**

Run:

```bash
PLAYWRIGHT_HEADLESS=0 venv/bin/python main.py run taneda \
  --size 1 \
  --release-at 11:00 \
  --newly-released-only \
  --exact-times "5:15 PM,7:45 PM" \
  --monitor-duration 1 \
  --dry-run
```

Expected: the process stays alive for the monitoring window instead of exiting after a single empty newly-released check.

- [ ] **Step 5: Final commit if needed**

```bash
git status
```

If verification required code/doc changes:

```bash
git add README.md main.py recon.py sniper.py tests/test_main.py tests/test_models.py tests/test_recon.py tests/test_sniper.py
git commit -m "test: verify monitoring and forward windows"
```

---

## Self-Review

- Spec coverage:
  - forward recon window is covered in Task 3 and Task 5
  - launch monitoring is covered in Task 4 and Task 7
  - explicit monitoring mode is covered in Task 2 and Task 4
  - date-range input is covered in Task 1 and Task 2
  - documentation updates are covered in Task 6
- Placeholder scan:
  - no `TODO`, `TBD`, or “handle appropriately” placeholders remain
- Type consistency:
  - `monitor`, `monitor_duration`, and `date_ranges` are used consistently across CLI and runtime tasks
