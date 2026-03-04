"""
T0ckSn1per — unified CLI
========================

Subcommands
-----------
  recon  <slug>           Discover available dates/times for a restaurant
  snipe  --config FILE    Load a saved config and start sniping
  run    <slug>           Recon then snipe in one shot

Examples
--------
  python main.py recon canlis --size 2 --save canlis.json
  python main.py snipe --config canlis.json [--dry-run]
  python main.py run   canlis --size 2 [--dry-run]
"""

import argparse
import asyncio
import json
import logging
import sys

from recon import recon, save_config, load_config
from sniper import snipe_all

# ── Logging ───────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)-12s] %(levelname)s: %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("t0cksn1per.log"),
    ],
)
log = logging.getLogger("main")


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
    tasks = load_config(args.config)
    if not tasks:
        log.error("No tasks found in '%s'.", args.config)
        sys.exit(1)
    log.info("Loaded %d task(s) from %s", len(tasks), args.config)
    await snipe_all(tasks, dry_run=args.dry_run)


async def _cmd_run(args: argparse.Namespace) -> None:
    tasks = await recon(args.slug, size=args.size)
    if not tasks:
        log.warning("Recon found no availability for '%s'. Nothing to snipe.", args.slug)
        sys.exit(1)
    if args.save:
        save_config(tasks, args.save)
    await snipe_all(tasks, dry_run=args.dry_run)


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
    p_snipe = sub.add_parser("snipe", help="Snipe using a pre-built config file")
    p_snipe.add_argument("--config", required=True, metavar="FILE", help="JSON config from recon")
    p_snipe.add_argument("--dry-run", action="store_true", help="Find slots but don't click")

    # run  (recon + snipe in one shot)
    p_run = sub.add_parser("run", help="Recon then snipe in one shot")
    p_run.add_argument("slug", help="Tock restaurant slug, e.g. 'canlis'")
    p_run.add_argument("--size", default="2", help="Party size (default: 2)")
    p_run.add_argument("--dry-run", action="store_true", help="Find slots but don't click")
    p_run.add_argument("--save", metavar="FILE", help="Also save recon config to JSON")

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
