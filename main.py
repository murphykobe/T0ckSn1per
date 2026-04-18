"""
T0ckSn1per — unified CLI
========================

Subcommands
-----------
  recon  <slug>           Discover available dates/times for a restaurant
  snipe  [slug]           Snipe using a config file or inline --target flags
  run    <slug>           Recon then snipe in one shot

Examples
--------
  python main.py recon canlis --size 2 --save canlis.json
  python main.py snipe --config canlis.json [--dry-run]
  python main.py snipe alinea --target 2026-03-15 "5:00 PM" "9:30 PM" 2 --dry-run --json
  python main.py run   canlis --size 2 [--dry-run]
"""

import argparse
import asyncio
import json
import logging
import sys

from models import LaunchConfig, Selector, Task, Target
from recon import recon, save_config, load_config
from sniper import snipe_all

# ── Logging ───────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)-12s] %(levelname)s: %(message)s",
    handlers=[
        logging.StreamHandler(sys.stderr),
        logging.FileHandler("t0cksn1per.log"),
    ],
)
log = logging.getLogger("main")


# ── Output helpers ────────────────────────────────────────────────────────────

def _print_results(results: list, json_mode: bool) -> None:
    import json as _json
    has_success = any(r.get("status") == "success" for r in results)
    has_error   = any(r.get("status") == "error" for r in results)

    if json_mode:
        output = results[0] if len(results) == 1 else (results if results else {})
        print(_json.dumps(output))
    else:
        for r in results:
            if r["status"] == "success":
                print(
                    f"\n{'='*60}\n"
                    f"  SLOT SECURED — complete checkout in your browser!\n"
                    f"  Restaurant : {r['restaurant']}\n"
                    f"  Date       : {r['date']}\n"
                    f"  Time       : {r.get('time', 'N/A')}\n"
                    f"  Checkout   : {r.get('checkout_url', 'N/A')}\n"
                    f"{'='*60}\n"
                )
            elif r["status"] == "error":
                print(f"[{r['restaurant']}] Error: {r.get('error', 'unknown')}", file=sys.stderr)
            else:
                print(f"[{r['restaurant']}] No slot found.", file=sys.stderr)

    if has_success:
        sys.exit(0)
    elif has_error:
        sys.exit(2)
    else:
        sys.exit(1)


def _split_csv_values(raw: str) -> list[str]:
    return [part.strip() for part in raw.split(",") if part.strip()]


def _collect_inline_values(args: argparse.Namespace, singular_name: str, plural_name: str) -> list[str]:
    values = list(getattr(args, singular_name, []) or [])
    plural_value = getattr(args, plural_name, None)
    if plural_value:
        values.extend(_split_csv_values(plural_value))
    return values


def _build_inline_task(args: argparse.Namespace) -> Task:
    target_args = getattr(args, "target", None)
    if target_args:
        selectors = [
            Selector(
                dates=[date],
                earliest_time=earliest,
                latest_time=latest,
            )
            for date, earliest, latest, _size in target_args
        ]
        size = target_args[0][3]
    else:
        dates = _collect_inline_values(args, "date", "dates")
        exact_times = _collect_inline_values(args, "exact_time", "exact_times")
        selectors = [
            Selector(
                dates=dates,
                exact_times=exact_times,
            )
        ]
        size = getattr(args, "size", "2")

    launch = None
    if getattr(args, "release_at", None):
        launch = LaunchConfig(
            release_at=args.release_at,
            newly_released_only=getattr(args, "newly_released_only", False),
        )

    return Task(url=args.slug, size=size, selectors=selectors, launch=launch)


# ── Subcommand handlers ───────────────────────────────────────────────────────

async def _cmd_recon(args: argparse.Namespace) -> None:
    tasks = await recon(args.slug, size=args.size)
    if not tasks:
        log.warning("No availability found for '%s'.", args.slug)
        sys.exit(1)
    if args.save:
        save_config(tasks, args.save)
        log.info("Config saved → %s", args.save)
    else:
        print(json.dumps([t.to_dict() for t in tasks], indent=2))


async def _cmd_snipe(args: argparse.Namespace) -> None:
    if args.config:
        tasks = load_config(args.config)
    elif args.target or args.date or getattr(args, "dates", None):
        if not args.slug:
            log.error("Inline targeting requires a restaurant slug as positional argument")
            sys.exit(2)
        tasks = [_build_inline_task(args)]
    else:
        log.error("Provide --config FILE, --target ..., or at least one --date/--dates")
        sys.exit(2)

    if not tasks:
        log.error("No tasks to run.")
        sys.exit(1)

    snipe_kwargs = dict(
        dry_run=args.dry_run,
        interval=args.interval,
        max_duration=args.max_duration,
        release_at=getattr(args, "release_at", None),
        cdp_url=getattr(args, "cdp_url", None),
        timezone=getattr(args, "timezone", None),
        cookies_file=getattr(args, "cookies_file", None),
        interactive_login=getattr(args, "login", False),
        prompt_login=getattr(args, "prompt_login", False),
    )
    results = await snipe_all(tasks, **snipe_kwargs)
    _print_results(results, json_mode=args.json)


async def _cmd_run(args: argparse.Namespace) -> None:
    if args.date or args.exact_time or getattr(args, "dates", None) or getattr(args, "exact_times", None):
        tasks = [_build_inline_task(args)]
    else:
        tasks = await recon(args.slug, size=args.size)
        if not tasks:
            log.warning("Recon found no availability for '%s'. Nothing to snipe.", args.slug)
            sys.exit(1)
        if args.save:
            save_config(tasks, args.save)
    snipe_kwargs = dict(
        dry_run=args.dry_run,
        interval=args.interval,
        max_duration=args.max_duration,
        release_at=getattr(args, "release_at", None),
        cdp_url=getattr(args, "cdp_url", None),
        timezone=getattr(args, "timezone", None),
        cookies_file=getattr(args, "cookies_file", None),
        interactive_login=getattr(args, "login", False),
        prompt_login=getattr(args, "prompt_login", False),
    )
    results = await snipe_all(tasks, **snipe_kwargs)
    _print_results(results, json_mode=args.json)


# ── Argument parser ───────────────────────────────────────────────────────────

def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="t0cksn1per",
        description="Concurrent Tock reservation sniper",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # recon
    p_recon = sub.add_parser("recon", help="Discover available dates for a restaurant")
    p_recon.add_argument("slug", help="Tock restaurant slug, e.g. 'canlis'")
    p_recon.add_argument("--size", default="2", help="Party size (default: 2)")
    p_recon.add_argument("--save", metavar="FILE", help="Save discovered config to JSON")

    # snipe
    p_snipe = sub.add_parser("snipe", help="Snipe using a config file or inline targets")
    p_snipe.add_argument("slug", nargs="?", default=None,
                         help="Tock restaurant slug (required for inline targeting)")
    p_snipe.add_argument("--config", metavar="FILE", help="JSON config from recon")
    p_snipe.add_argument(
        "--target",
        nargs=4,
        action="append",
        metavar=("DATE", "EARLIEST", "LATEST", "SIZE"),
        help="Inline target: DATE EARLIEST LATEST SIZE (repeatable)",
    )
    p_snipe.add_argument("--date", action="append", default=[],
                         help="Target calendar date YYYY-MM-DD (repeatable)")
    p_snipe.add_argument("--exact-time", action="append", default=[],
                         help="Exact reservation start time, e.g. '5:15 PM' (repeatable)")
    p_snipe.add_argument("--dates",
                         help="Comma-separated target dates, e.g. '2026-05-27,2026-05-28'")
    p_snipe.add_argument("--exact-times",
                         help="Comma-separated exact times, e.g. '5:15 PM,7:45 PM'")
    p_snipe.add_argument("--dry-run", action="store_true", help="Find slots but don't click")
    p_snipe.add_argument("--interval", type=float, default=30.0, metavar="SECONDS",
                         help="Poll interval in seconds (default: 30)")
    p_snipe.add_argument("--max-duration", type=float, default=0, metavar="MINUTES",
                         help="Stop after this many minutes (0 = unlimited)")
    p_snipe.add_argument("--release-at", metavar="HH:MM",
                         help="Start sniping at this time of day")
    p_snipe.add_argument("--cdp-url",
                         help="Advanced: connect to an existing Chrome/Chromium CDP endpoint")
    p_snipe.add_argument("--newly-released-only", action="store_true",
                         help="In launch mode, only target dates that appear after release")
    p_snipe.add_argument("--timezone", metavar="TZ",
                         help="Timezone for --release-at (e.g. America/Chicago)")
    p_snipe.add_argument("--cookies-file", metavar="FILE",
                         help="Path to Netscape cookies file for authentication")
    p_snipe.add_argument("--login", action="store_true",
                         help="Perform interactive browser login before sniping")
    p_snipe.add_argument("--prompt-login", action="store_true",
                         help="After cart add, prompt for Tock credentials to tie cart to your account")
    p_snipe.add_argument("--json", action="store_true",
                         help="Output result as JSON on stdout")

    # run  (recon + snipe in one shot)
    p_run = sub.add_parser("run", help="Recon then snipe in one shot")
    p_run.add_argument("slug", help="Tock restaurant slug, e.g. 'canlis'")
    p_run.add_argument("--size", default="2", help="Party size (default: 2)")
    p_run.add_argument("--date", action="append", default=[],
                       help="Target calendar date YYYY-MM-DD (repeatable)")
    p_run.add_argument("--exact-time", action="append", default=[],
                       help="Exact reservation start time, e.g. '5:15 PM' (repeatable)")
    p_run.add_argument("--dates",
                       help="Comma-separated target dates, e.g. '2026-05-27,2026-05-28'")
    p_run.add_argument("--exact-times",
                       help="Comma-separated exact times, e.g. '5:15 PM,7:45 PM'")
    p_run.add_argument("--dry-run", action="store_true", help="Find slots but don't click")
    p_run.add_argument("--save", metavar="FILE", help="Also save recon config to JSON")
    p_run.add_argument("--interval", type=float, default=30.0, metavar="SECONDS",
                       help="Poll interval in seconds (default: 30)")
    p_run.add_argument("--max-duration", type=float, default=0, metavar="MINUTES",
                       help="Stop after this many minutes (0 = unlimited)")
    p_run.add_argument("--release-at", metavar="HH:MM",
                       help="Start sniping at this time of day")
    p_run.add_argument("--cdp-url",
                       help="Advanced: connect to an existing Chrome/Chromium CDP endpoint")
    p_run.add_argument("--newly-released-only", action="store_true",
                       help="In launch mode, only target dates that appear after release")
    p_run.add_argument("--timezone", metavar="TZ",
                       help="Timezone for --release-at (e.g. America/Chicago)")
    p_run.add_argument("--cookies-file", metavar="FILE",
                       help="Path to Netscape cookies file for authentication")
    p_run.add_argument("--login", action="store_true",
                       help="Perform interactive browser login before sniping")
    p_run.add_argument("--prompt-login", action="store_true",
                       help="After cart add, prompt for Tock credentials to tie cart to your account")
    p_run.add_argument("--json", action="store_true",
                       help="Output result as JSON on stdout")

    return parser


# ── Entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()

    dispatch = {
        "recon": _cmd_recon,
        "snipe": _cmd_snipe,
        "run":   _cmd_run,
    }
    try:
        asyncio.run(dispatch[args.command](args))
    except KeyboardInterrupt:
        print("\n[t0cksn1per] Interrupted — shutting down.", file=sys.stderr)
        sys.exit(130)  # 128 + SIGINT(2)


if __name__ == "__main__":
    main()
