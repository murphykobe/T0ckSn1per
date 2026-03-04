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

# Dry-run: find slots but don't click (useful for testing)
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

## Configuration

Environment variables (all optional):

| Variable           | Default                          | Description                              |
|--------------------|----------------------------------|------------------------------------------|
| `REFRESH_DELAY_SEC`| `1.0`                            | Seconds between poll cycles per tab      |
| `CHROME_EXECUTABLE`| Playwright's bundled Chromium    | Path to a custom Chrome binary           |
| `TOCK_USERNAME`    | —                                | Tock account email (only if logging in)  |
| `TOCK_PASSWORD`    | —                                | Tock account password                    |

To enable login, set `ENABLE_LOGIN = True` in `sniper.py` and export the two env vars above.

---

## How it works

```
main.py  (CLI)
   │
   ├─ recon.py   ── opens one browser, scrapes the calendar month-by-month
   │                returns a list of Task objects (slug, party size, days, time window)
   │
   └─ sniper.py  ── opens one browser per Task
                    one tab per target day, all polling concurrently
                    first tab to find an open slot:
                      1. sets a shared asyncio.Event → all other tabs stop
                      2. notifies you (console banner + desktop popup + beep)
                      3. keeps the browser open for 10 min so you can finish checkout
```

---

## Running the tests

No browser or network required — all Playwright interactions are mocked.

```bash
pytest tests/ -v
```

| Test file              | What it covers                                           |
|------------------------|----------------------------------------------------------|
| `tests/test_models.py` | URL building, time window parsing, JSON serialisation    |
| `tests/test_recon.py`  | `_parse_time`, `_time_str`, `_build_tasks` + fallback    |
| `tests/test_sniper.py` | `DayWorker._try_time`: time window logic, dry-run, mocks |

---

## Project layout

```
main.py          ← unified CLI (recon / snipe / run)
recon.py         ← calendar scraper
sniper.py        ← async concurrent slot-clicker
models.py        ← Task dataclass, shared constants
notifier.py      ← console banner + desktop notification + beep
requirements.txt
tests/
  test_models.py
  test_recon.py
  test_sniper.py
```
