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

# 2. Install Python dependencies
pip install -r requirements.txt

# 3. Install Playwright's Chromium browser
playwright install chromium
```

All subsequent commands assume the venv is active (or use `venv/bin/python3` / `venv/bin/pytest` directly).

---

## Usage

There are three subcommands. The Tock restaurant **slug** is the path segment from the URL — e.g. for `https://www.exploretock.com/canlis/` the slug is `canlis`.

### `recon` — discover available dates

Scrapes the restaurant's Tock calendar and prints (or saves) a JSON task config.

```bash
# Print discovered availability
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

Loads a JSON config from `recon`, or accepts inline `--target` flags, and starts polling immediately.

```bash
# Live snipe from a config file
python main.py snipe --config canlis.json

# Inline targets (no config file needed)
python main.py snipe canlis \
  --target 2026-03-14 "5:00 PM" "9:30 PM" 2 \
  --target 2026-03-21 "5:00 PM" "9:30 PM" 2

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

# Dry-run
python main.py run canlis --size 2 --dry-run
```

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
                    first tab to find + click a slot:
                      1. sets a shared asyncio.Event → all other tabs stop
                      2. fires notifications (console banner + desktop popup + bell)
                      3. keeps the browser open for 10 min so you can finish checkout
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
| `--config FILE`   | JSON config file from `recon`                                      |
| `--interval SECONDS` | Poll interval in seconds (default: 30)                          |
| `--max-duration MINUTES` | Stop after this many minutes (0 = unlimited)                |
| `--release-at HH:MM` | Start sniping at this time of day                               |
| `--timezone TZ`   | Timezone for `--release-at` (e.g. `America/Chicago`)               |
| `--cookies-file FILE` | Path to Netscape cookies file for authentication               |
| `--login`         | Perform interactive browser login before sniping                   |
| `--prompt-login`  | Prompt for Tock credentials at startup                             |
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
| `tests/test_models.py` | URL building, time-window parsing, JSON round-trip |
| `tests/test_recon.py` | `_parse_time`, `_time_str`, `_build_tasks`, fallback window |
| `tests/test_sniper.py` | `DayWorker._try_time`: window logic, dry-run, found-event |

All Playwright interactions are replaced with `AsyncMock` — the suite runs in ~0.4 s.

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
