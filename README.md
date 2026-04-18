# T0ckSn1per

Concurrent Tock reservation sniper built on Playwright + asyncio. Opens one browser tab per target date and polls all of them simultaneously — the first tab that clicks an open slot notifies you to finish checkout before the hold expires.

---

## Requirements

- Python 3.9+
- A machine with internet access to `exploretock.com`
- A display for headed mode (recommended — Cloudflare is more aggressive in headless)

---

## Setup

```bash
# 1. Create and activate a virtual environment
python3 -m venv venv
source venv/bin/activate          # Windows: venv\Scripts\activate

# 2. Install as a package (editable mode for development)
pip install -e ".[dev,ai,notify]"

# 3. Install Playwright's Chromium browser
playwright install chromium
```

This installs a `t0cksn1per` CLI command. You can also run directly with `python main.py`.

For one-off runs without cloning or creating a venv first, you can execute the repo directly with `uvx`:

```bash
uvx --from git+https://github.com/murphykobe/T0ckSn1per t0cksn1per --help
```

All subsequent commands assume the venv is active (or use `venv/bin/python3` / `venv/bin/pytest` directly).

---

## Usage

There are three subcommands. The Tock restaurant **slug** is the path segment from the URL — e.g. for `https://www.exploretock.com/canlis/` the slug is `canlis`.

### `recon` — discover available dates

Scrapes the restaurant's Tock calendar and prints (or saves) a JSON task config.

```bash
# Print discovered availability (either form works)
t0cksn1per recon canlis --size 2
python main.py recon canlis --size 2

# Save to a file for later use with `snipe`
python main.py recon canlis --size 2 --save canlis.json
```

Sample output:

```json
[
  {
    "url": "canlis",
    "size": "2",
    "targets": [
      {"date": "2026-03-14", "earliest_time": "5:00 PM", "latest_time": "9:30 PM"},
      {"date": "2026-03-21", "earliest_time": "5:00 PM", "latest_time": "9:30 PM"}
    ]
  }
]
```

If `ANTHROPIC_API_KEY` is set, Claude will refine the time window. Otherwise a broad fallback (`11:00 AM – 11:30 PM`) is used — edit the JSON to tighten it before sniping.

---

### `snipe` — snipe from a saved config or inline targets

Loads a JSON config from `recon`, or accepts inline `--target` flags, compact `--dates` lists, and optional deterministic `--exact-times` values.

```bash
# Live snipe from a config file
python main.py snipe --config canlis.json

# Inline targets (no config file needed)
python main.py snipe canlis \
  --target 2026-03-14 "5:00 PM" "9:30 PM" 2 \
  --target 2026-03-21 "5:00 PM" "9:30 PM" 2

# Compact deterministic mode: try exact start times on a list of dates
python main.py snipe taneda \
  --dates 2026-06-17,2026-06-18 \
  --exact-times "5:15 PM,7:45 PM"

# Dry-run: finds slots but does not click them
python main.py snipe --config canlis.json --dry-run

# Output structured JSON on stdout
python main.py snipe --config canlis.json --json
```

When a slot is secured the browser stays open for **10 minutes** — complete checkout manually before Tock releases the hold.

---

### `run` — recon + snipe in one shot

```bash
python main.py run canlis --size 2

# Also save the discovered config
python main.py run canlis --size 2 --save canlis.json

# Release mode: wait until 11:00 in local machine time, then fire
python main.py run canlis --size 2 --release-at 11:00

# Taneda-style launch mode: only target newly released dates and hit exact slots
python main.py run taneda \
  --size 2 \
  --release-at 11:00 \
  --newly-released-only \
  --dates 2026-06-17,2026-06-18 \
  --exact-times "5:15 PM,7:45 PM"

# Attach to an existing local Chrome via CDP instead of launching a managed browser
python main.py run taneda \
  --size 2 \
  --release-at 11:00 \
  --newly-released-only \
  --dates 2026-06-17,2026-06-18 \
  --exact-times "5:15 PM,7:45 PM" \
  --cdp-url http://127.0.0.1:9222

# Dry-run with custom interval
python main.py run canlis --size 2 --dry-run --interval 15
```

`--release-at` uses local machine time by default. `--timezone` remains available as an advanced override, but most local runs do not need it.

The `run` subcommand accepts all the same flags as `snipe` (`--interval`, `--max-duration`, `--release-at`, `--newly-released-only`, `--dates`, `--exact-times`, `--timezone`, `--cookies-file`, `--login`, `--prompt-login`, `--json`, `--dry-run`).

---

## OpenClaw Skill

This repo includes an OpenClaw-ready skill at `.agents/skills/tock-sniper/SKILL.md`.

- use local plus headed mode when you want the browser on your Mac
- use node plus headless mode for unattended polling
- use CDP only when you explicitly want to attach to an existing local Chrome

The skill shells out to the repo CLI instead of reimplementing reservation logic:

```bash
uvx --from git+https://github.com/murphykobe/T0ckSn1per t0cksn1per --help
```

---

## CDP Mode

CDP is an advanced local-only mode for "use my existing Chrome on this Mac" workflows.

Start Chrome with remote debugging enabled:

```bash
"/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" \
  --remote-debugging-port=9222 \
  --user-data-dir=/tmp/tocksn1per-cdp
```

Then point `t0cksn1per` at that browser:

```bash
PLAYWRIGHT_HEADLESS=0 uvx --from git+https://github.com/murphykobe/T0ckSn1per \
  t0cksn1per run taneda \
  --size 2 \
  --release-at 11:00 \
  --newly-released-only \
  --dates 2026-06-17,2026-06-18 \
  --exact-times "5:15 PM,7:45 PM" \
  --cdp-url http://127.0.0.1:9222
```

If `--cdp-url` is omitted, the CLI keeps using its normal Playwright-managed browser.

---

## How it works

```
main.py  (CLI)
   │
   ├─ recon.py   ── opens one browser, loads the Tock search page
   │                waits for React to render the calendar
   │                scrapes available months, days, and sample time slots
   │                optionally refines results with Claude (ANTHROPIC_API_KEY)
   │                returns a list of Task objects
   │
   └─ sniper.py  ── opens one browser per Task
                    opens one tab per target day, all polling concurrently
                    each tab polls on a randomised interval (default 30 s ± 10% jitter)
                    each poll cycle:
                      1. loads the search page (domcontentloaded)
                      2. extracts __NEXT_DATA__ JSON (Next.js SSR data) for fast slot detection
                      3. if no matching slots in window → return immediately (skips DOM wait)
                      4. if matching slots found → fall through to DOM click flow
                      5. if __NEXT_DATA__ unavailable → fall back to DOM scraping
                    first tab to find + click a slot:
                      1. sets a shared asyncio.Event → all other tabs stop
                      2. verifies cart add succeeded (checkout URL / holding-time / confirmation text)
                      3. fires notifications (console banner + desktop popup + bell)
                      4. keeps the browser open for 10 min so you can finish checkout
```

**Anti-detection measures:**

- Non-headless Chrome by default (Cloudflare Turnstile is most aggressive in headless mode)
- `--disable-blink-features=AutomationControlled` launch flag
- `playwright-stealth` patches (`navigator.webdriver`, etc.) applied per page
- Randomised poll delay with ± jitter
- Realistic macOS Chrome User-Agent string

---

## Environment variables

All optional. Set in your shell or a `.env` file (loaded manually — no `python-dotenv` dependency).

| Variable              | Default                        | Description                                            |
|-----------------------|--------------------------------|--------------------------------------------------------|
| `PLAYWRIGHT_HEADLESS` | `0`                            | Set to `1` for headless mode (CI / no display)         |
| `CHROME_EXECUTABLE`   | Playwright's bundled Chromium  | Path to a custom Chrome binary                         |
| `ANTHROPIC_API_KEY`   | —                              | Enables Claude-assisted time-window refinement in recon|

---

## CLI Flags

### `snipe` subcommand

| Flag              | Description                                                        |
|-------------------|--------------------------------------------------------------------|
| `--target DATE EARLIEST LATEST SIZE` | Inline target (repeatable). Example: `--target 2026-03-14 "5:00 PM" "9:30 PM" 2` |
| `--dates YYYY-MM-DD,YYYY-MM-DD` | Comma-separated compact date filter |
| `--exact-times "H:MM AM/PM,H:MM AM/PM"` | Comma-separated deterministic exact start times |
| `--date YYYY-MM-DD` | Legacy repeatable date flag, still supported |
| `--exact-time "H:MM AM/PM"` | Legacy repeatable exact-time flag, still supported |
| `--config FILE`   | JSON config file from `recon`                                      |
| `--interval SECONDS` | Poll interval in seconds (default: 30)                          |
| `--max-duration MINUTES` | Stop after this many minutes (0 = unlimited)                |
| `--release-at HH:MM` | Start sniping at this local machine time                       |
| `--cdp-url URL`   | Advanced: connect to an existing Chrome/Chromium CDP endpoint     |
| `--newly-released-only` | In launch mode, target only dates that appear after release |
| `--timezone TZ`   | Optional advanced override for `--release-at`                      |
| `--cookies-file FILE` | Path to Netscape cookies file for authentication               |
| `--login`         | Perform interactive browser login before sniping                   |
| `--prompt-login`  | After cart add, prompt for Tock credentials to tie cart to your account |
| `--json`          | Output result as JSON on stdout                                    |
| `--dry-run`       | Find slots but do not click them                                   |

---

## Tests

### Unit tests — fast, no browser, no network

```bash
venv/bin/pytest tests/ -v
# or, skipping integration tests explicitly:
venv/bin/pytest tests/ --ignore=tests/integration -v
```

| File | What it covers |
|---|---|
| `tests/test_models.py` | URL building, time-window parsing, JSON round-trip (5 tests) |
| `tests/test_recon.py` | `_parse_time`, `_time_str`, `_build_tasks`, `__NEXT_DATA__` parser (19 tests) |
| `tests/test_sniper.py` | `DayWorker._try_time`, `_extract_next_data`, `_poll` pre-filter, `_any_slot_in_window`, cart verification, cookies (41 tests) |

All Playwright interactions are replaced with `AsyncMock` — the suite runs in ~2 s.

---

### Integration tests — real browser, live Tock site

Two tests live under `tests/integration/`:

#### Smoke test (`test_smoke.py`) — always passes when Tock is reachable

Verifies that the browser can load a Tock search page, the calendar renders, and month headings are parseable. Does **not** click anything.

```bash
PLAYWRIGHT_HEADLESS=0 venv/bin/pytest tests/integration/test_smoke.py -v -s
```

#### E2E slot-click test (`test_e2e.py`) — requires real availability

Full end-to-end flow: `recon` → find available slot → click it → assert Tock's cart UI appears. Skips cleanly when:
- No availability exists for the target restaurant today
- `exploretock.com` is unreachable (network blocked, proxy)

```bash
# Headed (watch the browser):
PLAYWRIGHT_HEADLESS=0 venv/bin/pytest tests/integration/test_e2e.py -v -s

# Headless (CI):
PLAYWRIGHT_HEADLESS=1 venv/bin/pytest tests/integration/test_e2e.py -v -s

# Override restaurant and party size:
TEST_TOCK_SLUG=canlis TEST_TOCK_SIZE=2 venv/bin/pytest tests/integration/test_e2e.py -v -s
```

**E2E environment variables:**

| Variable              | Default   | Description                        |
|-----------------------|-----------|------------------------------------|
| `PLAYWRIGHT_HEADLESS` | `0`       | `1` = headless                     |
| `TEST_TOCK_SLUG`      | `alinea`  | Restaurant slug to test against    |
| `TEST_TOCK_SIZE`      | `2`       | Party size                         |

**What the test does:**

| Step | Action |
|------|--------|
| 1 | `recon()` opens a real browser and scrapes the Tock calendar |
| 2 | `DayWorker._poll()` navigates to the search page and clicks the target day |
| 3 | `DayWorker._try_time()` clicks the first time slot in the acceptable window |
| 4 | Asserts Tock's cart/checkout UI is visible on the page |

The test does **not** complete checkout — Tock releases the cart hold automatically after ~10 minutes.

**Run all integration tests together:**

```bash
PLAYWRIGHT_HEADLESS=0 venv/bin/pytest tests/integration/ -v -s
```

---

## Project layout

```
main.py                  ← unified CLI (recon / snipe / run)
recon.py                 ← calendar scraper + optional Claude refinement
sniper.py                ← async concurrent slot-clicker
models.py                ← Task dataclass, shared constants
notifier.py              ← console banner + desktop notification + bell
requirements.txt
pytest.ini               ← asyncio_mode=auto, integration marker
t0cksn1per.log           ← runtime log (created on first run)
tests/
  test_models.py         ← unit: Task dataclass
  test_recon.py          ← unit: recon helpers
  test_sniper.py         ← unit: DayWorker logic (mocked Playwright)
  integration/
    conftest.py          ← browser / context / page fixtures + shared config
    test_smoke.py        ← always-passing calendar-render check
    test_e2e.py          ← full slot-click → cart assertion
```

---

## Logs

Every run appends to `t0cksn1per.log` in the working directory alongside timestamped stdout output. Adjust the log level in `main.py` if you want quieter output.

---

## Disclaimer

This tool interacts with a live website. Use it responsibly and in accordance with Tock's terms of service. The authors take no responsibility for bans, missed reservations, or unintended charges.
