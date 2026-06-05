#!/usr/bin/env python3
"""
Congress Mirror – entry point.

Commands
--------
  python run_mirror.py start          Scheduler loop (runs at 09:30 ET daily)
  python run_mirror.py start --dry-run  Same, but no real orders placed
  python run_mirror.py once           Single cycle right now
  python run_mirror.py once --dry-run Single cycle, no orders
  python run_mirror.py status         Print account balance + saved state
  python run_mirror.py rank           Print current politician ranking without trading
"""
import argparse
import json
import logging
import sys
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

logger = logging.getLogger(__name__)


def cmd_start(dry_run: bool) -> None:
    from congress_mirror.scheduler import start
    start(dry_run=dry_run)


def cmd_once(dry_run: bool) -> None:
    from congress_mirror.mirror import run_full_cycle
    from congress_mirror.notifier import send_summary

    summary = run_full_cycle(dry_run=dry_run)
    send_summary(summary)

    top = summary.get("top_performer") or {}
    print(f"\nTop performer : {top.get('name', '–')}")
    print(f"12-mo return  : {top.get('return', 0) * 100:.1f}%")
    print(f"Trade count   : {top.get('trade_count', 0)}")
    print(f"\nNew disclosures : {len(summary.get('new_disclosures', []))}")
    print(f"Trades executed : {len(summary.get('trades_executed', []))}")

    if summary.get("errors"):
        print("\nErrors:")
        for e in summary["errors"]:
            print(f"  {e}")


def cmd_status() -> None:
    from congress_mirror.alpaca_client import AlpacaClient

    client = AlpacaClient()
    account = client.get_account()
    positions = client.get_positions()

    print("=== Alpaca Paper Account ===")
    print(f"Portfolio value : ${account['portfolio_value']:>12,.2f}")
    print(f"Cash            : ${account['cash']:>12,.2f}")
    print(f"Buying power    : ${account['buying_power']:>12,.2f}")

    if positions:
        print(f"\nPositions ({len(positions)}):")
        print(f"  {'Symbol':<8} {'Qty':>8} {'Market Value':>14} {'P&L':>10}")
        print(f"  {'-'*8} {'-'*8} {'-'*14} {'-'*10}")
        for p in sorted(positions, key=lambda x: x["market_value"], reverse=True):
            print(
                f"  {p['symbol']:<8} {p['qty']:>8.2f} "
                f"${p['market_value']:>13,.2f} "
                f"${p['unrealized_pl']:>+9,.2f}"
            )
    else:
        print("\nNo open positions.")

    state_file = Path("congress_mirror_state.json")
    if state_file.exists():
        state = json.loads(state_file.read_text())
        print(f"\nLast run        : {state.get('last_run', '–')}")
        print(f"Mirroring       : {state.get('top_performer_name', '–')}")


def cmd_rank() -> None:
    from congress_mirror.capitol_trades import get_politician_trades, get_politicians
    from congress_mirror.returns import rank_politicians
    from congress_mirror import config

    print("Fetching politician list …")
    politicians = get_politicians()
    print(f"Found {len(politicians)} politicians. Fetching trades …")

    all_trades: dict = {}
    for pol in politicians:
        pol_id = pol["id"]
        try:
            trades = get_politician_trades(pol_id, days=config.LOOKBACK_DAYS)
            for t in trades:
                t["politician_name"] = pol.get("name", pol_id)
            if len([t for t in trades if t["type"] == "buy"]) >= config.MIN_TRADES:
                all_trades[pol_id] = trades
        except Exception as e:
            logger.warning("Skip %s: %s", pol_id, e)

    ranked = rank_politicians(all_trades, min_trades=config.MIN_TRADES)
    print(f"\n{'#':>3}  {'Name':<35} {'Return':>8}  Trades")
    print("-" * 60)
    for i, r in enumerate(ranked[:20], 1):
        print(f"{i:>3}. {r['name']:<35} {r['return']*100:>+7.1f}%  {r['trade_count']}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Congress Mirror – automated Congress-trade follower")
    sub = parser.add_subparsers(dest="command")

    p_start = sub.add_parser("start", help="Start daily scheduler (blocks)")
    p_start.add_argument("--dry-run", action="store_true", help="No real orders")

    p_once = sub.add_parser("once", help="Run one cycle immediately")
    p_once.add_argument("--dry-run", action="store_true", help="No real orders")

    sub.add_parser("status", help="Print account + state")
    sub.add_parser("rank",   help="Print politician ranking without trading")

    args = parser.parse_args()

    if args.command == "start":
        cmd_start(dry_run=args.dry_run)
    elif args.command == "once":
        cmd_once(dry_run=args.dry_run)
    elif args.command == "status":
        cmd_status()
    elif args.command == "rank":
        cmd_rank()
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
