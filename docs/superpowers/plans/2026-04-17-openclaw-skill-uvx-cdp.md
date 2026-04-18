# OpenClaw Skill, Uvx, And CDP Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `t0cksn1per` runnable via `uvx` from the Git repo, add a repo-hosted OpenClaw skill that shells out to the CLI, and add optional CDP browser attachment for local Mac execution.

**Architecture:** Keep the CLI as the source of truth and add the thinnest possible layers around it. The repo-hosted skill will generate one concrete command for local, node, or CDP mode. The runtime will gain one optional browser-connection path, `connect_over_cdp`, while reusing the existing worker orchestration after the browser context is created.

**Tech Stack:** Python 3.9+, argparse, asyncio, Playwright async API, uv/uvx-compatible packaging via `pyproject.toml`, Markdown skill docs, pytest

---

## File Structure

- Modify: `pyproject.toml`
  Add any missing package metadata needed for repo-based `uvx` execution.
- Modify: `main.py`
  Add `--cdp-url` to `snipe` and `run`, pass it into the runtime.
- Modify: `sniper.py`
  Split browser setup from worker orchestration, add CDP connection support, preserve existing behavior when CDP is absent.
- Create: `.agents/skills/tock-sniper/SKILL.md`
  Main OpenClaw skill entry point with concise routing and command-generation guidance.
- Create: `.agents/skills/tock-sniper/references/commands.md`
  Example command forms for local, node, launch, and exact-time usage.
- Create: `.agents/skills/tock-sniper/references/cdp.md`
  CDP setup details for local Chrome on macOS.
- Modify: `README.md`
  Document `uvx` repo execution and the new CDP flag.
- Modify: `tests/test_main.py`
  Cover `--cdp-url` parser wiring.
- Modify: `tests/test_sniper.py`
  Cover browser-setup branching and CDP attach behavior without launching a real browser.

### Task 1: Make Repo-Based Uvx Execution Explicit

**Files:**
- Modify: `pyproject.toml`
- Modify: `README.md`

- [ ] **Step 1: Write a failing packaging smoke note as a test target**

Add this command to the verification checklist in `README.md` so the expected invocation is explicit:

```md
uvx --from git+https://github.com/murphykobe/T0ckSn1per t0cksn1per --help
```

- [ ] **Step 2: Run the command to observe current behavior**

Run: `uvx --from . t0cksn1per --help`
Expected: If it fails, capture the missing metadata or build issue. If it passes already, note that no package-structure change is required.

- [ ] **Step 3: Add minimal package metadata only if required**

If `uvx --from . t0cksn1per --help` fails because of missing metadata, add only the minimal fields needed, for example:

```toml
[project]
readme = "README.md"
license = { text = "MIT" }
authors = [{ name = "murphykobe" }]
```

If the command already works, leave `pyproject.toml` unchanged.

- [ ] **Step 4: Update README with the repo-based `uvx` form**

Add a short section:

```md
## Run From Git

You can run the latest version directly from the repository with:

```bash
uvx --from git+https://github.com/murphykobe/T0ckSn1per t0cksn1per --help
```
```

- [ ] **Step 5: Verify the packaging path**

Run: `uvx --from . t0cksn1per --help`
Expected: PASS, with CLI help output.

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml README.md
git commit -m "docs: add repo-based uvx execution"
```

### Task 2: Add The CDP CLI Surface

**Files:**
- Modify: `main.py`
- Test: `tests/test_main.py`

- [ ] **Step 1: Write the failing parser test**

Add to `tests/test_main.py`:

```python
def test_parser_accepts_cdp_url_for_run():
    parser = _build_parser()

    args = parser.parse_args([
        "run",
        "taneda",
        "--size", "3",
        "--dates", "2026-05-27,2026-05-28",
        "--exact-times", "5:15 PM,7:45 PM",
        "--cdp-url", "http://127.0.0.1:9222",
    ])

    assert args.cdp_url == "http://127.0.0.1:9222"
```

- [ ] **Step 2: Run the targeted test to verify it fails**

Run: `venv/bin/pytest tests/test_main.py::test_parser_accepts_cdp_url_for_run -v`
Expected: FAIL with `unrecognized arguments: --cdp-url`.

- [ ] **Step 3: Add the CLI flag to both subcommands**

In `main.py`, add:

```python
p_snipe.add_argument("--cdp-url",
                     help="Advanced: connect to an existing Chrome/Chromium CDP endpoint")

p_run.add_argument("--cdp-url",
                   help="Advanced: connect to an existing Chrome/Chromium CDP endpoint")
```

- [ ] **Step 4: Pass `cdp_url` into `snipe_all()`**

Update both `snipe_kwargs` blocks:

```python
cdp_url=getattr(args, "cdp_url", None),
```

- [ ] **Step 5: Run the parser test**

Run: `venv/bin/pytest tests/test_main.py::test_parser_accepts_cdp_url_for_run -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add main.py tests/test_main.py
git commit -m "feat: add cdp-url CLI flag"
```

### Task 3: Add CDP Browser Attachment In The Runtime

**Files:**
- Modify: `sniper.py`
- Test: `tests/test_sniper.py`

- [ ] **Step 1: Write the failing runtime tests**

Add to `tests/test_sniper.py`:

```python
@pytest.mark.asyncio
async def test_build_browser_context_uses_cdp_when_url_provided():
    from sniper import _open_browser_context

    playwright = AsyncMock()
    remote_browser = AsyncMock()
    existing_context = AsyncMock()
    remote_browser.contexts = [existing_context]
    playwright.chromium.connect_over_cdp = AsyncMock(return_value=remote_browser)

    browser, context = await _open_browser_context(playwright, cdp_url="http://127.0.0.1:9222")

    playwright.chromium.connect_over_cdp.assert_awaited_once_with("http://127.0.0.1:9222")
    assert browser is remote_browser
    assert context is existing_context


@pytest.mark.asyncio
async def test_build_browser_context_launches_browser_when_cdp_missing():
    from sniper import _open_browser_context

    playwright = AsyncMock()
    browser = AsyncMock()
    context = AsyncMock()
    playwright.chromium.launch = AsyncMock(return_value=browser)
    browser.new_context = AsyncMock(return_value=context)

    opened_browser, opened_context = await _open_browser_context(playwright, cdp_url=None)

    playwright.chromium.launch.assert_awaited()
    browser.new_context.assert_awaited()
    assert opened_browser is browser
    assert opened_context is context
```

- [ ] **Step 2: Run the targeted tests to verify they fail**

Run: `venv/bin/pytest tests/test_sniper.py::test_build_browser_context_uses_cdp_when_url_provided tests/test_sniper.py::test_build_browser_context_launches_browser_when_cdp_missing -v`
Expected: FAIL because `_open_browser_context` does not exist.

- [ ] **Step 3: Add a focused browser-setup helper**

In `sniper.py`, add:

```python
async def _open_browser_context(playwright, cdp_url: Optional[str] = None):
    if cdp_url:
        browser = await playwright.chromium.connect_over_cdp(cdp_url)
        if browser.contexts:
            return browser, browser.contexts[0]
        context = await browser.new_context(user_agent=USER_AGENT)
        return browser, context

    launch_kwargs: dict = {"headless": HEADLESS, "args": ["--disable-blink-features=AutomationControlled"]}
    if CHROME_EXECUTABLE:
        launch_kwargs["executable_path"] = CHROME_EXECUTABLE
    browser: Browser = await playwright.chromium.launch(**launch_kwargs)
    context: BrowserContext = await browser.new_context(user_agent=USER_AGENT)
    return browser, context
```

- [ ] **Step 4: Use the helper inside `snipe_task()`**

Replace:

```python
        _launch_kwargs: dict = {"headless": HEADLESS, "args": ["--disable-blink-features=AutomationControlled"]}
        if CHROME_EXECUTABLE:
            _launch_kwargs["executable_path"] = CHROME_EXECUTABLE
        browser: Browser = await p.chromium.launch(**_launch_kwargs)
        context: BrowserContext = await browser.new_context(user_agent=USER_AGENT)
```

with:

```python
        browser, context = await _open_browser_context(p, cdp_url=cdp_url)
```

- [ ] **Step 5: Thread `cdp_url` through the runtime signatures**

Add `cdp_url: str = None` to:

```python
async def snipe_task(..., cdp_url=None) -> Optional[dict]:
async def snipe_all(..., cdp_url=None) -> list:
```

And pass it through:

```python
result = await snipe_task(..., cdp_url=cdp_url)
```

- [ ] **Step 6: Run the full sniper suite**

Run: `venv/bin/pytest tests/test_sniper.py -v`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add sniper.py tests/test_sniper.py
git commit -m "feat: support attaching to Chrome via CDP"
```

### Task 4: Add The Repo-Hosted OpenClaw Skill

**Files:**
- Create: `.agents/skills/tock-sniper/SKILL.md`
- Create: `.agents/skills/tock-sniper/references/commands.md`
- Create: `.agents/skills/tock-sniper/references/cdp.md`

- [ ] **Step 1: Create the skill frontmatter and concise trigger**

Create `.agents/skills/tock-sniper/SKILL.md`:

```md
---
name: tock-sniper
description: Use when the user wants to run the Tock reservation sniper for a timed release or cancellation watch, locally on their Mac or on a remote node.
---
```

- [ ] **Step 2: Add the core skill workflow**

Append:

```md
# Tock Sniper

## Quick Start

Generate one concrete `t0cksn1per` command and run it. Do not reimplement reservation logic in the skill.

Prefer:

- local + headed when the user wants a visible browser or checkout handoff
- node + headless when the user wants unattended polling
- CDP only when the user explicitly wants to use an existing local Chrome

## Command Source

Run the latest repo version with:

```bash
uvx --from git+https://github.com/murphykobe/T0ckSn1per t0cksn1per --help
```

## References

- For example commands, read `references/commands.md`
- For CDP setup, read `references/cdp.md`
```

- [ ] **Step 3: Add example command references**

Create `.agents/skills/tock-sniper/references/commands.md`:

```md
# Command Examples

## Local headed launch

```bash
PLAYWRIGHT_HEADLESS=0 uvx --from git+https://github.com/murphykobe/T0ckSn1per \
  t0cksn1per run taneda \
  --size 3 \
  --release-at 11:00 \
  --newly-released-only \
  --dates 2026-05-27,2026-05-28,2026-05-29,2026-05-30,2026-05-31 \
  --exact-times "5:15 PM,7:45 PM"
```

## Node headless launch

```bash
PLAYWRIGHT_HEADLESS=1 uvx --from git+https://github.com/murphykobe/T0ckSn1per \
  t0cksn1per run taneda \
  --size 1 \
  --release-at 11:00 \
  --newly-released-only \
  --dates 2026-05-27,2026-05-28,2026-05-29,2026-05-30,2026-05-31
```
```

- [ ] **Step 4: Add CDP reference**

Create `.agents/skills/tock-sniper/references/cdp.md`:

```md
# CDP Mode

Use CDP only when the user explicitly wants to run against an existing Chrome on their Mac.

Example local Chrome launch on macOS:

```bash
/Applications/Google\\ Chrome.app/Contents/MacOS/Google\\ Chrome \
  --remote-debugging-port=9222 \
  --user-data-dir=/tmp/tocksn1per-cdp
```

Example CLI usage:

```bash
PLAYWRIGHT_HEADLESS=0 uvx --from git+https://github.com/murphykobe/T0ckSn1per \
  t0cksn1per run taneda ... --cdp-url http://127.0.0.1:9222
```
```

- [ ] **Step 5: Verify the skill files are readable and concise**

Run: `sed -n '1,220p' .agents/skills/tock-sniper/SKILL.md .agents/skills/tock-sniper/references/commands.md .agents/skills/tock-sniper/references/cdp.md`
Expected: All files present, concise, and valid Markdown.

- [ ] **Step 6: Commit**

```bash
git add .agents/skills/tock-sniper
git commit -m "feat: add repo-hosted OpenClaw skill for t0cksn1per"
```

### Task 5: Verify End-To-End Packaging And CLI

**Files:**
- Modify: `README.md` if needed

- [ ] **Step 1: Run the repo-based `uvx` smoke check**

Run: `uvx --from . t0cksn1per --help`
Expected: PASS with the CLI help output.

- [ ] **Step 2: Run the local test suite**

Run: `venv/bin/pytest tests/ --ignore=tests/integration -q`
Expected: PASS.

- [ ] **Step 3: Run one live Playwright smoke test**

Run: `PLAYWRIGHT_HEADLESS=1 venv/bin/pytest tests/integration/test_smoke.py -q -s`
Expected: PASS with a live calendar load.

- [ ] **Step 4: If feasible, run a non-destructive CDP smoke test**

Start Chrome with:

```bash
/Applications/Google\\ Chrome.app/Contents/MacOS/Google\\ Chrome \
  --remote-debugging-port=9222 \
  --user-data-dir=/tmp/tocksn1per-cdp
```

Then run:

```bash
PLAYWRIGHT_HEADLESS=0 venv/bin/python main.py snipe surrell \
  --target 2026-05-13 "6:00 PM" "6:00 PM" 2 \
  --dry-run \
  --max-duration 0.1 \
  --cdp-url http://127.0.0.1:9222
```

Expected: CLI attaches to the existing browser and exits cleanly after the dry-run window.

- [ ] **Step 5: Commit any final doc or test adjustments**

```bash
git add README.md
git commit -m "test: verify uvx and cdp invocation paths"
```

## Self-Review

- Spec coverage:
  - repo-based `uvx` execution is covered in Task 1 and Task 5
  - repo-hosted skill files are covered in Task 4
  - optional CDP runtime support is covered in Task 2 and Task 3
  - local vs node execution guidance is covered in Task 4
- Placeholder scan:
  - no `TODO`, `TBD`, or deferred placeholders remain
  - each task includes concrete file paths, commands, and expected outcomes
- Type consistency:
  - `cdp_url` is the only new CLI/runtime field
  - `_open_browser_context()` is the single browser-setup abstraction
  - the skill remains a thin wrapper over `t0cksn1per`
