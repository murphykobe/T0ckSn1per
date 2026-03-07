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

from models import Task, Target
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
    elif args.target:
        if not args.slug:
            log.error("--target requires a restaurant slug as positional argument")
            sys.exit(2)
        targets = [
            Target(date=t[0], earliest_time=t[1], latest_time=t[2])
            for t in args.target
        ]
        tasks = [Task(url=args.slug, size=args.target[0][3], targets=targets)]
    else:
        log.error("Provide --config FILE or --target DATE EARLIEST LATEST SIZE")
        sys.exit(2)

    if not tasks:
        log.error("No tasks to run.")
        sys.exit(1)

    snipe_kwargs = dict(
        dry_run=args.dry_run,
        interval=args.interval,
        max_duration=args.max_duration,
        release_at=getattr(args, "release_at", None),
        timezone=getattr(args, "timezone", None),
        cookies_file=getattr(args, "cookies_file", None),
        interactive_login=getattr(args, "login", False),
        prompt_login=getattr(args, "prompt_login", False),
    )
    results = await snipe_all(tasks, **snipe_kwargs)
    _print_results(results, json_mode=args.json)


async def _cmd_run(args: argparse.Namespace) -> None:
    tasks = await recon(args.slug, size=args.size)
    if not tasks:
        log.warning("Recon found no availability for '%s'. Nothing to snipe.", args.slug)
        sys.exit(1)
    if args.save:
        save_config(tasks, args.save)
    results = await snipe_all(tasks, dry_run=args.dry_run, interval=args.interval)
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
                         help="Tock restaurant slug (required when using --target)")
    p_snipe.add_argument("--config", metavar="FILE", help="JSON config from recon")
    p_snipe.add_argument(
        "--target",
        nargs=4,
        action="append",
        metavar=("DATE", "EARLIEST", "LATEST", "SIZE"),
        help="Inline target: DATE EARLIEST LATEST SIZE (repeatable)",
    )
    p_snipe.add_argument("--dry-run", action="store_true", help="Find slots but don't click")
    p_snipe.add_argument("--interval", type=float, default=30.0, metavar="SECONDS",
                         help="Poll interval in seconds (default: 30)")
    p_snipe.add_argument("--max-duration", type=float, default=0, metavar="MINUTES",
                         help="Stop after this many minutes (0 = unlimited)")
    p_snipe.add_argument("--release-at", metavar="HH:MM",
                         help="Start sniping at this time of day")
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
    p_run.add_argument("--dry-run", action="store_true", help="Find slots but don't click")
    p_run.add_argument("--save", metavar="FILE", help="Also save recon config to JSON")
    p_run.add_argument("--interval", type=float, default=30.0, metavar="SECONDS",
                       help="Poll interval in seconds (default: 30)")
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
    asyncio.run(dispatch[args.command](args))


if __name__ == "__main__":
    main()
