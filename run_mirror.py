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
  python run_mirror.py diagnose       Test connectivity to data sources + Alpaca
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
    from congress_mirror import config
    from congress_mirror.capitol_trades import get_all_trades
    from congress_mirror.returns import rank_politicians

    print("Fetching congressional trade data …")
    all_trades = get_all_trades(days=config.LOOKBACK_DAYS)
    print(f"Loaded {len(all_trades)} politicians. Computing 12-month returns …")

    ranked = rank_politicians(all_trades, min_trades=config.MIN_TRADES)
    print(f"\n{'#':>3}  {'Name':<35} {'Return':>8}  Trades")
    print("-" * 60)
    for i, r in enumerate(ranked[:20], 1):
        print(f"{i:>3}. {r['name']:<35} {r['return']*100:>+7.1f}%  {r['trade_count']}")


def cmd_diagnose() -> None:
    """Test connectivity to each data source and the Alpaca API."""
    import socket

    import requests

    from congress_mirror.capitol_trades import (
        _UA,
        CAPITOL_BFF,
        HOUSE_SW_URL,
        SENATE_SW_URL,
    )

    print("=== DNS resolution ===")
    for host in ("house-stock-watcher-data.s3-us-west-2.amazonaws.com",
                 "senate-stock-watcher-data.s3-us-west-2.amazonaws.com",
                 "bff.capitoltrades.com",
                 "paper-api.alpaca.markets"):
        try:
            print(f"  OK   {host} -> {socket.gethostbyname(host)}")
        except Exception as e:
            print(f"  FAIL {host}: {e}")

    print("\n=== Data sources (HTTP) ===")
    for label, url in (("House Stock Watcher",  HOUSE_SW_URL),
                       ("Senate Stock Watcher", SENATE_SW_URL)):
        try:
            r = requests.get(url, timeout=60, headers={"User-Agent": _UA}, stream=True)
            n = len(r.json()) if r.status_code == 200 else 0
            print(f"  {label}: HTTP {r.status_code}  ({n} records)")
        except Exception as e:
            print(f"  {label}: ERROR {e}")

    try:
        sess = requests.Session()
        try:
            import cloudscraper
            sess = cloudscraper.create_scraper()
        except ImportError:
            print("  (cloudscraper not installed; capitoltrades likely blocked)")
        r = sess.get(CAPITOL_BFF, params={"page": 1, "pageSize": 5}, timeout=30,
                     headers={"User-Agent": _UA})
        print(f"  capitoltrades BFF: HTTP {r.status_code}")
    except Exception as e:
        print(f"  capitoltrades BFF: ERROR {e}")

    print("\n=== Alpaca ===")
    try:
        from congress_mirror.alpaca_client import AlpacaClient
        acct = AlpacaClient().get_account()
        print(f"  OK   buying power ${acct['buying_power']:,.2f}")
    except Exception as e:
        print(f"  FAIL {e}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Congress Mirror – automated Congress-trade follower")
    sub = parser.add_subparsers(dest="command")

    p_start = sub.add_parser("start", help="Start daily scheduler (blocks)")
    p_start.add_argument("--dry-run", action="store_true", help="No real orders")

    p_once = sub.add_parser("once", help="Run one cycle immediately")
    p_once.add_argument("--dry-run", action="store_true", help="No real orders")

    sub.add_parser("status",   help="Print account + state")
    sub.add_parser("rank",     help="Print politician ranking without trading")
    sub.add_parser("diagnose", help="Test connectivity to data sources + Alpaca")

    args = parser.parse_args()

    if args.command == "start":
        cmd_start(dry_run=args.dry_run)
    elif args.command == "once":
        cmd_once(dry_run=args.dry_run)
    elif args.command == "status":
        cmd_status()
    elif args.command == "rank":
        cmd_rank()
    elif args.command == "diagnose":
        cmd_diagnose()
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
