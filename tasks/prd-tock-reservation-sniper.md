# PRD: T0ckSn1per — Tock Reservation Sniper CLI

## Introduction

Tock reservation slots at high-demand restaurants are nearly impossible to book manually — they either sell out within seconds of a timed release, or briefly reappear when someone cancels. T0ckSn1per is a CLI bot that automates the watch-and-grab loop using a real browser (Playwright), targets multiple date/time windows simultaneously, and stops at "add to cart" so the user can complete checkout on their own account.

The tool is designed to be invoked directly by the user or by an AI agent.

---

## Goals

- Monitor Tock for reservation availability across multiple target date/time windows for a given restaurant
- Support two polling modes: **release mode** (hit hard at a specific clock time when reservations open) and **restock mode** (continuously poll for cancellation slots)
- Add the first matching slot to cart and stop — no payment info required
- Produce a clear exit signal (stdout + non-zero/zero exit code) so an agent or script can react
- Remain resilient to Tock UI changes by using `data-testid` and `aria-label` selectors over CSS classes
- Keep scope to a single-user CLI MVP; no web UI, no accounts, no database

---

## User Stories

### US-001: Multi-target configuration
**Description:** As a user, I want to specify multiple date and time-window targets in a single config or CLI invocation so that I can grab any available slot across several preferred dates/times.

**Acceptance Criteria:**
- [ ] Config accepts a list of targets: each target has `date` (YYYY-MM-DD or month/day/year), `earliest_time`, `latest_time`, and `party_size`
- [ ] All targets for a restaurant are polled concurrently (one async worker per target date)
- [ ] First successful cart add across any target wins; remaining workers stop
- [ ] CLI `--target` flag (repeatable) allows inline target specification without a config file
- [ ] Dry-run mode (`--dry-run`) logs what would be clicked without actually clicking

### US-002: Release mode (timed drop)
**Description:** As a user, I want the bot to wait until a specific release time and then immediately attempt to book, so I can compete with other users on a timed reservation drop.

**Acceptance Criteria:**
- [ ] `--release-at HH:MM` flag puts the bot in release mode
- [ ] Bot loads the restaurant page and pre-warms the browser session before the release time
- [ ] At `release_at`, all target-date workers simultaneously attempt to click and book
- [ ] If no slot appears within a configurable timeout, bot falls back to restock polling mode
- [ ] Release time is local time by default; `--timezone` flag overrides

### US-003: Restock/polling mode (cancellation watch)
**Description:** As a user, I want the bot to continuously poll for cancellation slots so I can catch a seat that someone else gave up.

**Acceptance Criteria:**
- [ ] Default mode (no `--release-at`) is continuous polling
- [ ] Poll interval is configurable (`--interval`, default 30 seconds)
- [ ] Each poll: navigate to date, check for available time slots matching targets, click if found
- [ ] Bot stops and exits 0 on first successful cart add
- [ ] Bot exits non-zero after `--max-duration` minutes with no success (default: unlimited)

### US-004: Add to cart and halt
**Description:** As a user, I want the bot to add the reservation to my Tock cart and then stop, so I can complete checkout myself with my saved payment info.

**Acceptance Criteria:**
- [ ] After clicking a time slot, bot detects cart/checkout confirmation (URL matches `/checkout/`, `data-testid="holding-time"` present, or "Complete your reservation" text)
- [ ] Bot prints the matched slot details (restaurant, date, time, party size) and the checkout URL to stdout
- [ ] Bot exits with code 0
- [ ] Browser window remains open (non-headless) so user can complete checkout, OR session cookies are exported (see US-006)
- [ ] In headless mode, bot prints explicit instructions for the user to continue

### US-005: Session authentication (bring your own login)
**Description:** As a user, I want the bot to use my existing Tock login so that the cart is tied to my account, not an anonymous session.

**Acceptance Criteria:**
- [ ] Bot accepts a `--cookies-file PATH` flag pointing to a Netscape-format cookie file
- [ ] Cookies are loaded into the browser context before any navigation
- [ ] If no cookie file is provided, bot opens the login page and waits for the user to log in manually (with a `--login` flag)
- [ ] Bot validates auth by checking for a logged-in indicator before starting to poll

### US-006: Post-cart login prompt (on-demand credential collection)
**Description:** As a user, I want the bot to optionally prompt me for my Tock credentials after adding to cart so I can log in without a pre-existing cookie file, and then open real Chrome to complete checkout on my saved payment info.

**Background:** Tock cart state is **server-side and account-tied** (confirmed live). A user logged into the same account on any device will see the same active cart. This means the optimal flow is: bot adds slot to cart (logged in as user) → user opens real Chrome → user logs in → cart is already there.

**Acceptance Criteria:**
- [ ] After a successful cart add, bot prints: "Slot secured! Open exploretock.com/{restaurant} in your browser and log in to complete checkout."
- [ ] If `--prompt-login` flag is set, bot pauses and prompts for email + password via stdin (using `getpass` for password masking)
- [ ] Bot uses the provided credentials to log in within the existing browser session, then prints the checkout URL
- [ ] If no credentials provided within 60 seconds (timeout configurable), bot exits 0 with instructions to continue manually
- [ ] `--cookies-file` path (pre-loaded cookies) remains supported as the faster alternative for repeat use

### US-007: Structured CLI output for agent use
**Description:** As an AI agent invoking this tool, I want machine-readable output so I can parse results and take follow-up actions.

**Acceptance Criteria:**
- [ ] `--json` flag emits a single JSON object to stdout on exit: `{"status": "success"|"no_slots"|"error", "restaurant": "...", "date": "...", "time": "...", "checkout_url": "..."}`
- [ ] Exit code 0 = cart add succeeded; 1 = no slots found in time; 2 = error/crash
- [ ] All human-readable logs go to stderr so stdout remains parseable

---

## Functional Requirements

- FR-1: Accept restaurant slug (e.g., `alinea`, `taneda`) as the primary positional argument
- FR-2: Accept one or more targets via config file or `--target DATE EARLIEST LATEST PARTY_SIZE`
- FR-3: Launch Chromium via Playwright with anti-detection args (`--disable-blink-features=AutomationControlled`), optionally non-headless
- FR-4: In release mode, sleep until `release_at - 30s`, pre-load the page, then fire all workers simultaneously at `release_at`
- FR-5: In restock mode, poll each target date on the configurable interval; use `data-testid="consumer-calendar-day"` and `aria-label="YYYY-MM-DD"` to navigate the calendar
- FR-6: Match available time slots using `data-testid="search-result"` cards and `data-testid="search-result-time"` inner text
- FR-7: Click `button[data-testid="booking-card-button"]` on matching slot to trigger cart add
- FR-8: Detect cart success via URL pattern `/checkout/` or `data-testid="holding-time"`
- FR-9: Print result to stdout and exit with appropriate code
- FR-10: Support `CHROME_EXECUTABLE` env var to use a custom Chrome binary
- FR-11: Support `PLAYWRIGHT_HEADLESS=0` env var for visible browser

---

## Non-Goals

- No automatic checkout / payment — bot stops at cart
- No web UI or dashboard
- No multi-user accounts or auth management
- No support for restaurant platforms other than Tock (exploretock.com)
- No mobile browser emulation
- No CAPTCHA solving
- No persistent database or reservation history

---

## Technical Considerations

- **Runtime:** Python 3.10+, `playwright` (async API), `playwright-stealth`
- **Concurrency:** `asyncio` with one `DayWorker` coroutine per target date, cancelled on first success
- **Selectors:** Use `data-testid` and `aria-label` exclusively — no CSS class selectors (Tock uses MUI which churns class names)
- **Config format:** TOML or JSON; one `[restaurant]` block and a list of `[[targets]]`
- **Virtual env:** all dependencies installed in `venv/`; entry point `venv/bin/python3 sniper.py`
- **Existing code to build on:** `sniper.py` (core bot), `recon.py` (discovery), `tests/integration/test_e2e.py`

---

## Success Metrics

- Bot successfully adds a slot to cart within 5 seconds of slot availability appearing on page
- Works against at least: Alinea, Taneda (confirmed via E2E tests)
- CLI can be invoked from a shell script or AI agent with zero manual interaction (cookie file pre-provided)
- Zero test regressions in existing unit + integration suite

---

## Open Questions

- **OQ-1:** ~~Is Tock cart state server-side (account-tied) or device-local?~~ **RESOLVED:** Cart is server-side and account-tied. User confirmed: logging into the same Tock account on a second device shows the active cart immediately. No cookie export needed — just log in on real Chrome after bot secures the slot.
- **OQ-2:** Does Tock enforce any rate limiting or bot detection on rapid calendar polling? Current stealth approach (non-headless, playwright-stealth, realistic UA) seems sufficient — monitor for CAPTCHAs or 429s in production use.
- **OQ-3:** Are there internal Tock API endpoints (REST/GraphQL) that could be polled directly instead of via UI automation? **Partial answer:** [neo](https://github.com/4ier/neo) is a Chrome extension that intercepts and replays any web app's `fetch()`/XHR traffic. Workflow: install neo, browse Tock normally (check calendar, click dates), neo captures all network calls and auto-generates an API schema. Those endpoints could then replace the Playwright UI loop with direct HTTP calls — dramatically faster and more reliable. **Planned as a future optimization (post-MVP).**
