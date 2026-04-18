# Launch And Exact Targeting Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add compact date-list targeting, deterministic exact-time matching, and newly-released-only launch mode while preserving the existing broad window workflows.

**Architecture:** Keep the current Playwright polling engine, but extend the data model so compact selectors can expand into concrete execution targets. Resolve launch-mode date deltas inside the runtime using a warmed browser session, then feed the resulting eligible dates into the same concurrent worker path the project already uses.

**Tech Stack:** Python 3.9+, argparse, asyncio, Playwright async API, pytest, dataclasses

---

## File Structure

- Modify: `models.py`
  Add `Selector` and `LaunchConfig`, extend `Target` for exact-match mode, and teach `Task` how to expand selectors into concrete targets.
- Modify: `main.py`
  Parse new inline flags, build selector-based tasks, and keep legacy `--target` flows working.
- Modify: `sniper.py`
  Add exact-match semantics, launch-date delta resolution, and local-time default release handling.
- Modify: `recon.py`
  Emit selector-based fallback tasks for newly discovered dates and preserve save/load compatibility.
- Modify: `README.md`
  Document compact launch mode and local-time defaults.
- Modify: `tests/test_models.py`
  Cover selector parsing, launch config parsing, and target expansion.
- Modify: `tests/test_sniper.py`
  Cover exact-time matching, launch-date delta logic, and the async warning regression.
- Modify: `tests/test_recon.py`
  Cover selector-based task building and backward compatibility.

### Task 1: Evolve The Data Model

**Files:**
- Modify: `models.py`
- Test: `tests/test_models.py`

- [ ] **Step 1: Write the failing tests**

```python
def test_selector_expands_dates_and_exact_times():
    from models import Selector

    selector = Selector(
        dates=["2026-06-17", "2026-06-18"],
        exact_times=["5:15 PM", "7:45 PM"],
    )

    targets = selector.expand_targets()

    assert [(t.date, t.exact_time) for t in targets] == [
        ("2026-06-17", "5:15 PM"),
        ("2026-06-17", "7:45 PM"),
        ("2026-06-18", "5:15 PM"),
        ("2026-06-18", "7:45 PM"),
    ]
    assert all(t.earliest_time == t.latest_time for t in targets)


def test_task_from_dict_accepts_selector_shape():
    from models import Task

    task = Task.from_dict({
        "url": "taneda",
        "size": "2",
        "launch": {"release_at": "11:00", "newly_released_only": True},
        "selectors": [
            {
                "dates": ["2026-06-17"],
                "exact_times": ["5:15 PM", "7:45 PM"],
            }
        ],
    })

    assert task.launch is not None
    assert task.launch.release_at == "11:00"
    assert task.launch.newly_released_only is True
    assert len(task.expand_targets()) == 2


def test_task_from_dict_translates_legacy_targets():
    from models import Task

    task = Task.from_dict({
        "url": "canlis",
        "size": "2",
        "targets": [
            {
                "date": "2026-03-15",
                "earliest_time": "5:00 PM",
                "latest_time": "9:30 PM",
            }
        ],
    })

    selector = task.selectors[0]
    assert selector.dates == ["2026-03-15"]
    assert selector.earliest_time == "5:00 PM"
    assert selector.latest_time == "9:30 PM"
```

- [ ] **Step 2: Run the model tests to verify they fail**

Run: `venv/bin/pytest tests/test_models.py -v`
Expected: `ImportError` or `AttributeError` for missing `Selector`, missing `launch`, or missing `expand_targets`.

- [ ] **Step 3: Write the minimal model implementation**

```python
@dataclass
class LaunchConfig:
    release_at: str
    newly_released_only: bool = True

    @classmethod
    def from_dict(cls, data: dict) -> "LaunchConfig":
        return cls(
            release_at=data["release_at"],
            newly_released_only=data.get("newly_released_only", True),
        )


@dataclass
class Selector:
    dates: list[str]
    earliest_time: str | None = None
    latest_time: str | None = None
    exact_times: list[str] = field(default_factory=list)

    def expand_targets(self) -> list["Target"]:
        if self.exact_times:
            return [
                Target(
                    date=date,
                    earliest_time=exact_time,
                    latest_time=exact_time,
                    exact_time=exact_time,
                )
                for date in self.dates
                for exact_time in self.exact_times
            ]

        return [
            Target(
                date=date,
                earliest_time=self.earliest_time or "12:00 PM",
                latest_time=self.latest_time or "11:00 PM",
            )
            for date in self.dates
        ]


@dataclass
class Target:
    date: str
    earliest_time: str
    latest_time: str
    exact_time: str | None = None


@dataclass
class Task:
    url: str
    size: str
    selectors: list[Selector] = field(default_factory=list)
    launch: LaunchConfig | None = None

    def expand_targets(self) -> list[Target]:
        targets: list[Target] = []
        for selector in self.selectors:
            targets.extend(selector.expand_targets())
        return targets

    @classmethod
    def from_dict(cls, d: dict) -> "Task":
        selectors_data = d.get("selectors")
        if selectors_data is None:
            selectors_data = [
                {
                    "dates": [target["date"]],
                    "earliest_time": target["earliest_time"],
                    "latest_time": target["latest_time"],
                }
                for target in d.get("targets", [])
            ]
        return cls(
            url=d["url"],
            size=d["size"],
            selectors=[Selector(**s) for s in selectors_data],
            launch=LaunchConfig.from_dict(d["launch"]) if d.get("launch") else None,
        )
```

- [ ] **Step 4: Run the model tests to verify they pass**

Run: `venv/bin/pytest tests/test_models.py -v`
Expected: PASS for the new selector/launch tests and the existing URL/time tests.

- [ ] **Step 5: Commit**

```bash
git add models.py tests/test_models.py
git commit -m "feat: add selector-based task model"
```

### Task 2: Parse Compact CLI Inputs

**Files:**
- Modify: `main.py`
- Test: `tests/test_models.py`

- [ ] **Step 1: Write a failing helper test for inline selector construction**

```python
def test_task_inline_selector_defaults_to_any_time_when_exact_times_missing():
    from models import Selector, Task

    task = Task(
        url="taneda",
        size="2",
        selectors=[Selector(dates=["2026-06-17"])],
    )

    targets = task.expand_targets()

    assert len(targets) == 1
    assert targets[0].date == "2026-06-17"
    assert targets[0].earliest_time == "12:00 PM"
    assert targets[0].latest_time == "11:00 PM"
```

- [ ] **Step 2: Run the targeted test to verify the broad fallback is not wired yet**

Run: `venv/bin/pytest tests/test_models.py::test_task_inline_selector_defaults_to_any_time_when_exact_times_missing -v`
Expected: FAIL if `Selector.expand_targets()` does not yet fall back to all-day broad targeting.

- [ ] **Step 3: Add CLI helpers in `main.py`**

```python
def _build_inline_task(args: argparse.Namespace) -> Task:
    if args.target:
        selectors = [
            Selector(
                dates=[date],
                earliest_time=earliest,
                latest_time=latest,
            )
            for date, earliest, latest, _size in args.target
        ]
    else:
        selectors = [
            Selector(
                dates=args.date or [],
                exact_times=args.exact_time or [],
            )
        ]

    launch = None
    if getattr(args, "release_at", None):
        launch = LaunchConfig(
            release_at=args.release_at,
            newly_released_only=getattr(args, "newly_released_only", False),
        )

    return Task(url=args.slug, size=args.size, selectors=selectors, launch=launch)
```

- [ ] **Step 4: Update parser arguments to expose the new flags**

```python
p_snipe.add_argument("--date", action="append", default=[], help="Target calendar date YYYY-MM-DD")
p_snipe.add_argument("--exact-time", action="append", default=[], help="Exact reservation start time")
p_snipe.add_argument("--newly-released-only", action="store_true",
                     help="In launch mode, only target dates that appear after release")

p_run.add_argument("--date", action="append", default=[], help="Target calendar date YYYY-MM-DD")
p_run.add_argument("--exact-time", action="append", default=[], help="Exact reservation start time")
p_run.add_argument("--newly-released-only", action="store_true",
                   help="In launch mode, only target dates that appear after release")
```

- [ ] **Step 5: Wire `snipe` and `run` to use inline selectors when config is absent**

```python
elif args.target or args.date:
    if not args.slug:
        log.error("Inline targeting requires a restaurant slug.")
        sys.exit(2)
    tasks = [_build_inline_task(args)]
```

- [ ] **Step 6: Run the fast model and CLI-adjacent tests**

Run: `venv/bin/pytest tests/test_models.py tests/test_recon.py -v`
Expected: PASS, with no regressions in legacy target parsing.

- [ ] **Step 7: Commit**

```bash
git add main.py tests/test_models.py
git commit -m "feat: add compact inline date and exact-time flags"
```

### Task 3: Implement Exact-Time Matching In The Worker

**Files:**
- Modify: `sniper.py`
- Test: `tests/test_sniper.py`

- [ ] **Step 1: Write the failing exact-match tests**

```python
@pytest.mark.asyncio
async def test_exact_time_clicks_only_exact_match():
    page = _make_page([
        _make_slot_element("5:00 PM"),
        _make_slot_element("5:15 PM"),
        _make_slot_element("7:45 PM"),
    ])
    target = Target(
        date="2026-06-17",
        earliest_time="5:15 PM",
        latest_time="5:15 PM",
        exact_time="5:15 PM",
    )
    worker = _make_worker(target=target, page=page)

    result = await worker._try_time()

    assert result is True
    page.query_selector_all.return_value[0].click.assert_not_awaited()
    page.query_selector_all.return_value[1].click.assert_awaited_once()


@pytest.mark.asyncio
async def test_parse_next_data_json_returns_none_for_non_dict():
    from sniper import _parse_next_data_json
    assert _parse_next_data_json(AsyncMock()) is None
```

- [ ] **Step 2: Run the targeted sniper tests to verify they fail**

Run: `venv/bin/pytest tests/test_sniper.py::test_exact_time_clicks_only_exact_match tests/test_sniper.py::test_parse_next_data_json_returns_none_for_non_dict -v`
Expected: FAIL because exact-time matching is not explicit and `_parse_next_data_json` still accepts non-dict values.

- [ ] **Step 3: Add exact-match helpers to `Target` and `DayWorker`**

```python
def matches_time(self, time_text: str) -> bool:
    if self.exact_time:
        return time_text == self.exact_time

    try:
        candidate = datetime.strptime(time_text, RESERVATION_TIME_FORMAT)
    except ValueError:
        return False
    return self.earliest_dt() <= candidate <= self.latest_dt()
```

```python
def _any_slot_matches_target(self, time_strings: list[str]) -> bool:
    return any(self.target.matches_time(time_text) for time_text in time_strings)
```

```python
if not isinstance(next_data, dict):
    return None
```

- [ ] **Step 4: Replace the broad-only checks inside `_poll()` and `_try_time()`**

```python
next_data_slots = await self._extract_next_data()
if next_data_slots is not None and not self._any_slot_matches_target(next_data_slots):
    return False

...

time_text = (await time_el.inner_text()).strip()
if not self.target.matches_time(time_text):
    continue
```

- [ ] **Step 5: Run the sniper unit suite**

Run: `venv/bin/pytest tests/test_sniper.py -v`
Expected: PASS, and the prior runtime warning disappears from the `test_day_worker_polls_immediately_first_iteration` path.

- [ ] **Step 6: Commit**

```bash
git add sniper.py tests/test_sniper.py
git commit -m "feat: add deterministic exact-time slot matching"
```

### Task 4: Resolve Newly Released Dates In Launch Mode

**Files:**
- Modify: `sniper.py`
- Test: `tests/test_sniper.py`

- [ ] **Step 1: Write the failing launch-delta tests**

```python
def test_newly_released_dates_applies_delta_and_filter():
    from sniper import _newly_released_dates

    result = _newly_released_dates(
        before={"2026-06-10", "2026-06-11"},
        after={"2026-06-10", "2026-06-11", "2026-06-17", "2026-06-18"},
        requested_dates=["2026-06-17", "2026-06-19"],
    )

    assert result == ["2026-06-17"]


def test_task_filter_dates_preserves_exact_times():
    selector = Selector(
        dates=["2026-06-17", "2026-06-18"],
        exact_times=["5:15 PM", "7:45 PM"],
    )
    task = Task(url="taneda", size="2", selectors=[selector])

    filtered = task.filter_dates(["2026-06-18"])

    assert [t.date for t in filtered.expand_targets()] == ["2026-06-18", "2026-06-18"]
```

- [ ] **Step 2: Run the targeted launch tests to verify they fail**

Run: `venv/bin/pytest tests/test_sniper.py::test_newly_released_dates_applies_delta_and_filter tests/test_models.py::test_task_filter_dates_preserves_exact_times -v`
Expected: FAIL for missing `_newly_released_dates` and missing `Task.filter_dates`.

- [ ] **Step 3: Add date filtering helpers to the model**

```python
def filter_dates(self, eligible_dates: list[str]) -> "Task":
    allowed = set(eligible_dates)
    selectors = []
    for selector in self.selectors:
        kept_dates = [date for date in selector.dates if date in allowed]
        if kept_dates:
            selectors.append(Selector(
                dates=kept_dates,
                earliest_time=selector.earliest_time,
                latest_time=selector.latest_time,
                exact_times=list(selector.exact_times),
            ))
    return Task(url=self.url, size=self.size, selectors=selectors, launch=self.launch)
```

- [ ] **Step 4: Add launch-delta helpers to `sniper.py`**

```python
async def _capture_available_dates(page: Page, slug: str, size: str) -> set[str]:
    url = f"https://www.exploretock.com/{slug}/search?date={datetime.now():%Y-%m}-01&size={size}&time=19%3A00"
    await page.goto(url, wait_until="domcontentloaded", timeout=PAGE_LOAD_TIMEOUT)
    await page.wait_for_selector("div.ConsumerCalendar-month", timeout=PAGE_LOAD_TIMEOUT)
    await page.wait_for_timeout(2000)
    buttons = await page.query_selector_all(
        "button[data-testid='consumer-calendar-day'][aria-disabled='false']"
    )
    dates: set[str] = set()
    for button in buttons:
        label = await button.get_attribute("aria-label")
        if label and len(label) == 10:
            dates.add(label)
    return dates


def _newly_released_dates(before: set[str], after: set[str], requested_dates: list[str] | None = None) -> list[str]:
    eligible = sorted(after - before)
    if requested_dates:
        requested = set(requested_dates)
        eligible = [date for date in eligible if date in requested]
    return eligible
```

- [ ] **Step 5: Move launch resolution into `snipe_task()` before worker creation**

```python
resolved_task = task
if task.launch and task.launch.release_at and task.launch.newly_released_only:
    scout = await context.new_page()
    await _apply_stealth(scout)
    before_dates = await _capture_available_dates(scout, task.url, task.size)
    await _wait_for_release(task.launch.release_at)
    after_dates = await _capture_available_dates(scout, task.url, task.size)
    requested_dates = sorted({date for selector in task.selectors for date in selector.dates})
    eligible_dates = _newly_released_dates(before_dates, after_dates, requested_dates)
    if not eligible_dates:
        await browser.close()
        return None
    resolved_task = task.filter_dates(eligible_dates)
```

- [ ] **Step 6: Build workers from `resolved_task.expand_targets()` instead of the old `task.targets`**

```python
expanded_targets = resolved_task.expand_targets()
for i, target in enumerate(expanded_targets):
    page = await context.new_page()
    await _apply_stealth(page)
    workers.append(
        DayWorker(
            resolved_task,
            target,
            page,
            found_event,
            dry_run=dry_run,
            interval=interval,
            prompt_login=prompt_login,
        )
    )
```

- [ ] **Step 7: Run the model and sniper tests together**

Run: `venv/bin/pytest tests/test_models.py tests/test_sniper.py -v`
Expected: PASS for launch delta logic, filtered selector expansion, and exact-time matching.

- [ ] **Step 8: Commit**

```bash
git add models.py sniper.py tests/test_models.py tests/test_sniper.py
git commit -m "feat: target only newly released launch dates"
```

### Task 5: Keep Recon And Docs In Sync

**Files:**
- Modify: `recon.py`
- Modify: `README.md`
- Test: `tests/test_recon.py`

- [ ] **Step 1: Write the failing recon selector test**

```python
def test_build_tasks_uses_selector_shape():
    avail = {
        "2026-03-01": {"time_slots": ["5:00 PM", "9:00 PM"]},
        "2026-03-15": {"time_slots": ["6:00 PM", "8:30 PM"]},
    }

    tasks = _build_tasks("taneda", "2", avail)

    assert len(tasks) == 1
    assert tasks[0].selectors[0].dates == ["2026-03-01", "2026-03-15"]
    assert tasks[0].selectors[0].exact_times == []
```

- [ ] **Step 2: Run the recon test to verify it fails**

Run: `venv/bin/pytest tests/test_recon.py::test_build_tasks_uses_selector_shape -v`
Expected: FAIL because `_build_tasks()` still emits only legacy `Target` entries.

- [ ] **Step 3: Update `_build_tasks()` to emit selector-based tasks**

```python
def _build_tasks(slug: str, size: str, availability: Dict[str, dict]) -> List[Task]:
    if not availability:
        return []

    selectors: list[Selector] = []
    for date_str, data in availability.items():
        slots = [_parse_time(t) for t in data.get("time_slots", []) if _parse_time(t)]
        if slots:
            selectors.append(Selector(
                dates=[date_str],
                earliest_time=_time_str(min(slots)),
                latest_time=_time_str(max(slots)),
            ))
        else:
            selectors.append(Selector(dates=[date_str]))

    return [Task(url=slug, size=size, selectors=selectors)]
```

- [ ] **Step 4: Update README examples for compact launch mode**

```md
python main.py run taneda \
  --size 2 \
  --release-at 11:00 \
  --newly-released-only \
  --date 2026-06-17 \
  --date 2026-06-18 \
  --exact-time "5:15 PM" \
  --exact-time "7:45 PM"
```

```md
`--release-at` uses the local machine time by default.
```

- [ ] **Step 5: Run the full fast test suite**

Run: `venv/bin/pytest tests/ --ignore=tests/integration -v`
Expected: PASS with all unit tests green.

- [ ] **Step 6: Run the live smoke test**

Run: `PLAYWRIGHT_HEADLESS=1 venv/bin/pytest tests/integration/test_smoke.py -q -s`
Expected: PASS with calendar render confirmation on a live Tock page.

- [ ] **Step 7: Commit**

```bash
git add recon.py README.md tests/test_recon.py
git commit -m "docs: add compact launch-mode targeting examples"
```

## Self-Review

- Spec coverage:
  - compact date lists are implemented in Task 1 and Task 2
  - exact-time deterministic matching is implemented in Task 1 and Task 3
  - newly released date targeting is implemented in Task 4
  - local-time `release_at` behavior is preserved and documented in Task 4 and Task 5
  - backward compatibility for legacy target configs is covered in Task 1 and Task 5
- Placeholder scan:
  - no `TODO`, `TBD`, or “implement later” markers remain
  - each code-changing step includes concrete code blocks
- Type consistency:
  - `LaunchConfig`, `Selector`, `Task.expand_targets()`, `Task.filter_dates()`, `_newly_released_dates()`, and `Target.exact_time` are used consistently across tasks
