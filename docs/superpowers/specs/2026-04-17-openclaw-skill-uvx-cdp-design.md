# OpenClaw Skill, Uvx Packaging, And CDP Design

## Context

`T0ckSn1per` is now a working Python CLI with:

- an installable console entry point, `t0cksn1per`
- launch mode, exact-time targeting, and compact date list support
- a Playwright-managed browser flow that has been validated against live Tock pages

The next step is to make this easy to invoke from OpenClaw as a reusable skill, without requiring a manual clone-and-venv workflow. The user also wants an advanced mode where the automation can run against a Chrome instance on their Mac through CDP rather than always launching a managed Playwright browser.

## Goals

- Package the CLI so it can be executed directly from the Git repository using `uvx`
- Add a repo-hosted `SKILL.md` that shells out to the CLI instead of reimplementing reservation logic
- Support two execution targets in the skill:
  - local execution on the user's Mac
  - remote execution on a node
- Add an advanced optional CDP mode that lets the CLI attach to an existing Chrome instance
- Keep Playwright-managed browser launch as the default path

## Non-Goals

- Publishing to PyPI
- Rewriting the skill to drive browser actions directly instead of using the CLI
- Making CDP the default execution path
- Solving remote browser orchestration beyond selecting where the command runs

## User-Facing Model

The system should have three layers:

1. CLI
   `t0cksn1per` remains the source of truth for reservation behavior.
2. Packaging
   `uvx --from git+https://github.com/<owner>/T0ckSn1per t0cksn1per ...` runs the latest repo version directly.
3. Skill
   The OpenClaw skill gathers intent and translates it into one CLI invocation.

The browser runs wherever the CLI command runs:

- if OpenClaw runs the command locally, the browser opens on the Mac
- if OpenClaw runs the command on a node, the browser opens on that node

## Packaging Design

The repo should be runnable with `uvx` directly from Git.

Target command form:

```bash
uvx --from git+https://github.com/<owner>/T0ckSn1per t0cksn1per --help
```

Implementation requirements:

- keep `pyproject.toml` as the package entry point definition
- ensure all runtime dependencies needed by `t0cksn1per` are installable from the repo
- add any missing package metadata only if needed for `uvx` execution
- do not require a tagged release or PyPI publication

The package should always use the latest default branch state for now.

## Skill Design

The skill should live inside this repository as a public, reusable artifact.

Recommended structure:

```text
openclaw-skill/
  SKILL.md
  references/
    commands.md
    cdp.md
```

The skill should stay thin. It should:

- identify whether the user wants a launch run, restock run, or exact-time run
- identify whether to run locally or on a node
- identify whether CDP is requested
- generate one command
- execute that command using the appropriate environment

The skill should not duplicate booking logic, parsing logic, or reservation heuristics from the CLI.

## Execution Targets

### Local Mode

Use local mode when:

- the user wants a visible browser on their Mac
- the user wants to complete checkout manually after a hold
- the user wants to reuse a local Chrome session via CDP

Preferred default:

```bash
PLAYWRIGHT_HEADLESS=0 uvx --from git+https://github.com/<owner>/T0ckSn1per \
  t0cksn1per run ...
```

### Node Mode

Use node mode when:

- the user wants unattended polling
- the environment is remote
- visibility on the local Mac is not required

Preferred default:

```bash
PLAYWRIGHT_HEADLESS=1 uvx --from git+https://github.com/<owner>/T0ckSn1per \
  t0cksn1per run ...
```

## CDP Design

CDP is an advanced optional browser-connection mode for local execution.

User-facing contract:

- add `--cdp-url http://127.0.0.1:9222`
- if present, the CLI connects to an existing browser instead of launching a new managed browser
- all reservation logic after browser connection stays the same

Example:

```bash
PLAYWRIGHT_HEADLESS=0 uvx --from git+https://github.com/<owner>/T0ckSn1per \
  t0cksn1per run taneda \
  --size 3 \
  --release-at 11:00 \
  --newly-released-only \
  --dates 2026-05-27,2026-05-28,2026-05-29,2026-05-30,2026-05-31 \
  --exact-times "5:15 PM,7:45 PM" \
  --cdp-url http://127.0.0.1:9222
```

Intended use:

- local Mac browser reuse
- session continuity
- easier visible handoff during checkout

Constraints:

- CDP mode is optional, not required
- CDP mode is not the primary path for remote node execution
- CDP setup is manual and may fail if Chrome is not started with remote debugging enabled

## Runtime Behavior

Default browser connection path:

- `async_playwright()`
- `p.chromium.launch(...)`
- `browser.new_context(...)`

CDP browser connection path:

- `async_playwright()`
- `p.chromium.connect_over_cdp(cdp_url)`
- select or create a usable browser context
- continue through the existing worker orchestration path

The rest of the runtime should not care whether the browser was launched or attached.

## CLI Additions

Add one new optional flag to `snipe` and `run`:

- `--cdp-url URL`
  Connect to an existing Chrome/Chromium debugging endpoint instead of launching a managed browser.

The help text should clearly describe this as an advanced/local option.

## Skill Responsibilities

The skill should translate user intent into one of these command families:

- local headed Playwright
- node headless Playwright
- local headed CDP

It should gather:

- restaurant slug
- party size
- date preferences or no preference
- exact times or broad mode
- launch time if any
- local or node execution target
- whether CDP is requested

Then it should produce one concrete command and run it.

## References To Include In The Skill

The skill should keep the main `SKILL.md` concise and move details into references:

- `references/commands.md`
  Example commands for launch mode, restock mode, local mode, node mode
- `references/cdp.md`
  How to start Chrome with remote debugging and when to use CDP

## Testing

Required verification for this work:

- unit or parser tests for the new `--cdp-url` flag
- a packaging smoke check proving `uvx --from git+... t0cksn1per --help` works
- a local CLI smoke check proving the command generation examples stay valid
- if feasible, a non-destructive CDP attach smoke test against a locally started browser

## Rollout

Recommended rollout order:

1. Make repo-based `uvx` execution work
2. Add the repo-hosted OpenClaw skill
3. Add CDP as an advanced browser-connection mode
4. Document local vs node execution clearly in the skill

## Resolved Decisions

- The skill should live inside this repository
- `uvx` should execute directly from the repository, not from PyPI
- Using the latest repo state is acceptable for now; tags are not required
- Playwright-managed browser remains the default
- CDP is an advanced optional mode for using the user's Mac browser
