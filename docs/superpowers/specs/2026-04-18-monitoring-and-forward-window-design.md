# Monitoring And Forward Window Design

## Context

The current sniper has two gaps that make it weak for live Tock drops and cancellation monitoring:

- `recon` only inspects the current visible month, so regular `run` can miss target dates in the next month.
- launch mode with `--newly-released-only` performs a single before/after diff at release time and exits if no newly released eligible dates are immediately actionable.

The user wants a more battle-ready model:

- launch mode should keep monitoring after release instead of giving up after one pass
- regular mode should support long-running refresh behavior for sold-out dates that may return when holds expire
- date targeting should support explicit date ranges and sensible defaults when the user does not provide dates
- restaurant-specific knowledge like Taneda seatings and open days can live in the skill/preset layer, while the CLI core stays generic

## Goals

- Make forward-looking recon scan beyond the current visible month.
- Add persistent monitoring behavior for launch and non-launch workflows.
- Support compact date-range input in the CLI.
- Keep exact-time targeting compatible with both launch and monitoring workflows.
- Preserve a generic core runtime while allowing richer restaurant presets in the skill layer.

## Non-Goals

- Hardcode Taneda-specific business rules directly into the sniper core.
- Build natural-language date parsing into the raw CLI.
- Replace the current worker model with a separate orchestration service.

## Recommended Approach

Use one unified forward-looking targeting model:

- both regular recon and monitoring flows use a configurable forward window instead of a single visible month
- launch mode uses release-aware date discovery plus a post-release monitoring window
- regular monitoring mode uses the same worker engine without release-time semantics
- explicit dates and explicit date ranges override default windows

This keeps one mental model across the product and avoids splitting launch and monitoring into separate engines.

## Default Windows

### Launch Mode

- If the user provides exact dates or date ranges, those dates are the launch target set.
- If the user does not provide dates, launch mode defaults to the next `30` calendar days.
- If `--newly-released-only` is set, newly released dates are filtered inside that target window.
- Launch monitoring should continue for `15` minutes by default after release.

### Regular Mode

- Regular recon and monitoring should look ahead `60` calendar days by default.
- If explicit dates or date ranges are provided, those override the default forward window.
- If monitoring is enabled, the sniper continues polling eligible dates instead of exiting after the initial recon snapshot.

## CLI Input Model

Keep the existing flags:

- `--dates 2026-05-21,2026-05-22`
- `--exact-times "5:15 PM,7:45 PM"`

Add date-range support:

- `--date-ranges "2026-05-07:2026-05-09,2026-05-21:2026-05-25"`

The raw CLI should use ISO-like explicit syntax for reliability. The OpenClaw skill can accept more natural user input and translate it into CLI-safe `--dates` or `--date-ranges`.

## Runtime Modes

### Launch Mode

Launch mode is triggered by `--release-at`.

Behavior:

- pre-warm browser state before release
- compute the eligible date window from explicit dates, explicit date ranges, or the default next `30` days
- if `--newly-released-only` is set, continue discovering newly released dates after release within the target window
- once eligible dates appear, keep polling them for slot availability and hold returns
- if `--exact-times` is provided, only those exact start times are actionable
- if exact times are omitted, any matching slot on eligible dates is acceptable
- default monitoring duration is `15` minutes unless explicitly overridden

Launch mode should not stop after a single empty diff. It should keep checking until the monitoring duration expires or a slot is secured.

### Regular Monitoring Mode

Add an explicit monitoring mode, for example `--monitor`.

Behavior:

- no release-time semantics
- find eligible dates in the explicit target set or the default forward recon window
- keep polling those dates for the monitoring duration
- if exact times are provided, only those start times are actionable
- if no exact times are provided, any in-window slot is acceptable

This is the mode for sold-out dates that may reappear from expired holds.

## Recon Behavior

`recon` should stop being “current visible month only.”

Instead:

- it should scan a forward window, defaulting to `60` days
- it should collect all eligible visible dates inside that window
- it should continue to sample time slots when possible
- if time slots cannot be sampled, it can still fall back to the broad time window

This change fixes the current Taneda issue where regular `run` only discovers a near-term visible date instead of the intended forward window.

## Data Model Changes

The current `Selector` shape can remain the canonical targeting model, but the CLI should be able to build selectors from:

- explicit dates
- expanded date ranges
- empty dates plus a forward-window mode

The runtime will need a way to represent:

- monitoring enabled or disabled
- monitoring duration
- lookahead days for forward scanning

These can live either in CLI/runtime kwargs or in a small config object without requiring a full architecture rewrite.

## Skill Layer Responsibilities

The OpenClaw skill or future presets can add restaurant-specific convenience such as:

- inferring Taneda’s typical seating times like `5:15 PM` and `7:45 PM`
- understanding open days like Wed–Sun
- translating natural date-range requests into explicit CLI flags

The core CLI should remain generic and should not encode restaurant-specific schedules.

## Error Handling And UX

Improve the CLI feedback for ambiguous commands:

- exact times without dates and without launch/monitoring context should produce a clear usage error
- launch mode with no date preferences should be interpreted as “use the default launch window,” not as zero workers
- monitoring mode with no dates should be interpreted as “use the default monitoring window,” not as zero workers

The logs should clearly distinguish:

- no eligible dates found yet
- eligible dates found but no matching slots yet
- monitoring window expired

## Testing Strategy

Add coverage for:

- date-range parsing into explicit dates
- launch mode defaulting to next `30` days when no dates are supplied
- regular monitoring defaulting to a forward window instead of a current-month-only recon snapshot
- launch mode continuing past the first empty newly-released diff
- monitoring mode continuing to poll until the monitoring duration expires
- exact-time filtering remaining deterministic inside launch and monitoring loops

## Summary

The core product change is:

- always look forward, not just at the current month
- treat launch mode as a persistent release watcher, not a one-shot diff
- add an explicit monitoring mode for cancellation and restock watching
- keep restaurant intelligence in the skill layer, not the core runtime
