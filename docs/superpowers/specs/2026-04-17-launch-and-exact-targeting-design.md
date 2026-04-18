# Launch And Exact Targeting Design

## Context

`T0ckSn1per` already supports:

- `recon` to discover currently visible dates
- `snipe` to poll for any slot inside a configured time window
- `run` to combine recon and sniping with an optional `--release-at`

For Taneda-style drops, the current model is too loose. The user knows the reservation start times in advance and wants the bot to act deterministically at release, rather than scanning broad time windows or targeting already-visible dates.

## Goals

- Support a compact way to target a list of dates without requiring the user to enumerate every date/time pair manually
- Support deterministic exact-time attacks such as `5:15 PM` and `7:45 PM`
- Keep existing broad window matching for users who do not know exact times
- Make launch mode operate only on newly released dates, not inventory that was already visible before the drop
- Interpret `release_at` in local machine time by default

## Non-Goals

- Replacing the existing `recon` or broad polling flows
- Requiring the user to think about timezone names for normal local runs
- Automatically inferring restaurant-specific release rules from the network or site

## User-Facing Model

The system supports two independent axes:

- Launch mode:
  Wait until a configured release time, then target only newly released dates.
- Exact mode:
  Attempt only exact reservation start times, rather than matching any slot within a time range.

These do not conflict. A run can be:

- Broad restock mode: no launch mode, no exact times
- Broad launch mode: launch mode enabled, no exact times
- Deterministic launch mode: launch mode enabled, exact times supplied
- Deterministic non-launch mode: exact times supplied without launch mode

## Config Shape

The task schema should evolve from only `targets` with one date and one broad range per entry to a more compact selector-oriented model.

Recommended shape:

```json
[
  {
    "url": "taneda",
    "size": "2",
    "launch": {
      "release_at": "11:00",
      "newly_released_only": true
    },
    "selectors": [
      {
        "dates": ["2026-06-17", "2026-06-18"],
        "exact_times": ["5:15 PM", "7:45 PM"]
      }
    ]
  }
]
```

Behavior:

- `dates` is a list of acceptable calendar dates
- `exact_times` is optional
- if `exact_times` is present, matching is deterministic and exact
- if `exact_times` is omitted, the bot accepts any slot that matches the date selector
- `launch.release_at` is optional; when present, the bot enters launch mode
- `launch.newly_released_only` defaults to `true` when launch mode is used

## CLI Shape

The CLI should support both config-driven and inline usage.

Recommended inline flags:

- `--date YYYY-MM-DD`
  Repeatable. Adds an acceptable target date.
- `--exact-time "H:MM AM/PM"`
  Repeatable. Enables exact-match mode.
- `--release-at HH:MM`
  Uses local machine time by default.
- `--newly-released-only`
  Enabled explicitly in CLI. For launch mode, this is the normal path.

Example:

```bash
python main.py run taneda \
  --size 2 \
  --release-at 11:00 \
  --newly-released-only \
  --date 2026-06-17 \
  --date 2026-06-18 \
  --exact-time "5:15 PM" \
  --exact-time "7:45 PM"
```

This means:

- wait until `11:00` in local machine time
- determine which dates are newly released at that moment
- narrow to the user-provided date list if one was supplied
- try exact start times `5:15 PM` and `7:45 PM` concurrently on any remaining target dates

## Local Time Default

`release_at` should use the local machine timezone by default.

User experience:

- if the user says `11:00`, it means `11:00` on the machine that runs the bot
- timezone names should not be required for normal usage

Implementation guidance:

- keep optional timezone override support only as an advanced/internal capability if needed
- do not make timezone selection part of the primary UX

## Runtime Semantics

### Broad Matching

If no `exact_times` are supplied:

- the bot may use any slot that appears on the allowed date set
- in non-launch mode, this behaves like the current date-plus-window behavior, though selectors may be broader
- in launch mode, it must still limit itself to newly released dates only

### Exact Matching

If `exact_times` are supplied:

- the bot must not scan a broad time range first
- it should attempt only the listed exact start times
- exact-time attempts should run concurrently
- the first successful cart add wins and stops the rest

### Launch Mode

When `release_at` is present:

1. Open and warm the browser session before release time
2. Capture the set of currently visible dates before the release
3. At the release moment, refresh or re-fetch availability
4. Compute the difference between post-release visible dates and pre-release visible dates
5. Treat only that delta as the eligible date set
6. If the user supplied a date list, intersect it with the newly released date set
7. Execute broad or exact matching against the resulting dates

This prevents the bot from wasting effort on already-visible inventory during a timed launch.

## Data Model Evolution

The current `Target` model represents one date with one time window. That shape should evolve to support compact user intent.

Recommended direction:

- add a higher-level selector model that can represent:
  - multiple dates
  - optional exact times
  - optional broad window fallback
- keep backward compatibility by translating old target entries into the new internal selector form

Backward compatibility examples:

- Existing target:
  - one date
  - `earliest_time`
  - `latest_time`
- New selector:
  - many dates
  - optional `exact_times`
  - optional broad matching fields if exact times are absent

## Execution Strategy

Internally, the runtime can still expand compact user input into concrete worker attempts.

Examples:

- `dates = [d1, d2]` and `exact_times = [t1, t2]`
  expands to four exact attempts:
  - `d1 x t1`
  - `d1 x t2`
  - `d2 x t1`
  - `d2 x t2`
- `dates = [d1, d2]` and no `exact_times`
  expands to two date-level broad workers:
  - `d1`
  - `d2`

This keeps the input compact while preserving concurrency in the execution layer.

## Error Handling

- If launch mode produces no newly released dates, exit cleanly with a no-slots result
- If launch mode plus user date filters leaves no eligible dates, exit cleanly with a no-slots result
- If exact times are supplied but none of them appear for the eligible dates, continue polling according to launch/restock semantics until success or timeout
- If both old broad-window fields and `exact_times` are supplied for the same selector, exact-time semantics should take precedence and the configuration should warn clearly in logs

## Testing

Required coverage:

- unit tests for config parsing of selectors, dates, and exact times
- unit tests for backward compatibility with existing target-based configs
- unit tests for exact-match filtering behavior
- unit tests for launch-mode delta computation between pre-release and post-release date sets
- unit tests for intersection of newly released dates with user-supplied date filters
- integration coverage that exercises launch-mode date-set computation without risking real slot checkout

## Rollout

Implementation should preserve current workflows while adding the new compact targeting path.

The first ship target is battle readiness for Taneda-style drops:

- local-time `release_at`
- newly released dates only
- exact start times such as `5:15 PM` and `7:45 PM`
- compact date list input

## Open Decisions Resolved

- Launch mode and exact mode are independent axes and can be combined
- The system should accept a list of dates, not force one entry per date/time pair
- If the user does not provide exact times, the bot should accept any slot on the eligible date set
- In launch mode, the eligible date set is only the newly released dates
- `release_at` uses local machine time by default
