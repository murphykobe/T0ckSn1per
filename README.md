# T0ckSn1per

Concurrent Tock reservation sniper built on Playwright + asyncio.
Spawns one browser tab per target date and polls all of them simultaneously — the first tab that finds an open slot grabs it and notifies you to finish checkout.

---

## Install

```bash
pip install -r requirements.txt
playwright install chromium
```

---

## Usage

### 1 — Recon (discover available dates)

Scrapes the restaurant's Tock calendar and returns a JSON task config.

```bash
# Print discovered availability to stdout
python main.py recon canlis --size 2

# Save to a file for later sniping
python main.py recon canlis --size 2 --save canlis.json
```

### 2 — Snipe (from a saved config)

Load a config produced by `recon` and start polling immediately.

```bash
python main.py snipe --config canlis.json

# Dry-run: find slots but don't click
python main.py snipe --config canlis.json --dry-run
```

### 3 — Run (recon + snipe in one shot)

```bash
python main.py run canlis --size 2

# Save the recon config AND snipe
python main.py run canlis --size 2 --save canlis.json

# Dry-run
python main.py run canlis --size 2 --dry-run
```

---

## Environment variables

All optional. Set in your shell or a `.env` file.

| Variable             | Default                       | Description                                        |
|----------------------|-------------------------------|----------------------------------------------------|
| `REFRESH_DELAY_SEC`  | `1.0`                         | Seconds between poll cycles per tab                |
| `CHROME_EXECUTABLE`  | Playwright's bundled Chromium | Path to a custom Chrome binary                     |
| `PLAYWRIGHT_HEADLESS`| `0`                           | Set to `1` for headless mode (CI / no display)     |
| `TOCK_USERNAME`      | —                             | Tock account email (only needed if logging in)     |
| `TOCK_PASSWORD`      | —                             | Tock account password                              |

> To enable login set `ENABLE_LOGIN = True` in `sniper.py` and export the two credential vars above.

---

## How it works

```
main.py  (CLI)
   │
   ├─ recon.py   ── opens one browser, scrapes the calendar month-by-month
   │                returns a list of Task objects (slug, party size, days, time window)
   │                optionally refines results with Claude if ANTHROPIC_API_KEY is set
   │
   └─ sniper.py  ── opens one browser per Task
                    one tab per target day, all polling concurrently
                    first tab to find an open slot:
                      1. sets a shared asyncio.Event → all other tabs stop
                      2. notifies you (console banner + desktop popup + beep)
                      3. keeps the browser open for 10 min so you can finish checkout
```

---

## Tests

### Unit tests (no browser, no network — fast)

```bash
pytest tests/ -v
```

| File                   | What it covers                                            |
|------------------------|-----------------------------------------------------------|
| `tests/test_models.py` | URL building, time window parsing, JSON serialisation     |
| `tests/test_recon.py`  | `_parse_time`, `_time_str`, `_build_tasks` + fallback     |
| `tests/test_sniper.py` | `DayWorker._try_time`: window logic, dry-run, found-event |

All Playwright interactions are replaced with `AsyncMock` — tests run in ~0.4 s.

### Integration / e2e test (real browser, live Tock site)

**Exit criteria:** the bot clicks a real Tock time slot and Tock's cart UI appears on the page. This is the only assertion that matters.

```bash
# Headed — watch the browser work:
PLAYWRIGHT_HEADLESS=0 pytest tests/integration/test_e2e.py -v -s

# Headless — CI / containers without a display:
PLAYWRIGHT_HEADLESS=1 pytest tests/integration/test_e2e.py -v -s

# Override the test restaurant:
TEST_TOCK_SLUG=canlis pytest tests/integration/test_e2e.py -v -s
```

**What the test does:**

| Step | Action |
|------|--------|
| 1 | `recon()` opens a real browser and scrapes the Tock calendar |
| 2 | `DayWorker._poll()` navigates to the search page and clicks an available day |
| 3 | `DayWorker._try_time()` clicks the first time slot in the acceptable window (`dry_run=False`) |
| 4 | After the click, asserts Tock's cart/checkout UI is visible on the page |

The test **does not complete checkout** — Tock releases the hold automatically after ~10 minutes.
The test skips cleanly if `exploretock.com` is unreachable (blocked proxy, no network).

**e2e environment variables:**

| Variable          | Default    | Description                          |
|-------------------|------------|--------------------------------------|
| `PLAYWRIGHT_HEADLESS` | `0`    | `1` = headless (no display needed)   |
| `TEST_TOCK_SLUG`  | `alinea`   | Restaurant slug to test against      |
| `TEST_TOCK_SIZE`  | `2`        | Party size                           |

---

## Project layout

```
main.py              ← unified CLI (recon / snipe / run)
recon.py             ← calendar scraper + optional Claude refinement
sniper.py            ← async concurrent slot-clicker
models.py            ← Task dataclass, shared constants
notifier.py          ← console banner + desktop notification + beep
requirements.txt
pytest.ini           ← asyncio_mode=auto, integration marker
tests/
  test_models.py     ← unit tests: models
  test_recon.py      ← unit tests: recon helpers
  test_sniper.py     ← unit tests: DayWorker logic (mocked Playwright)
  integration/
    conftest.py      ← browser/context/page fixtures
    test_e2e.py      ← end-to-end: real browser → cart assertion
```
