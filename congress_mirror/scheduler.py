"""
Weekday scheduler: runs the mirror cycle at 9:30 AM ET (market open).

Usage:
    python run_mirror.py start          # run scheduler in foreground
    python run_mirror.py once           # single cycle right now
    python run_mirror.py status         # print account + current state
"""
from __future__ import annotations

import logging
import time
from datetime import datetime

import pytz
import schedule

from .mirror import run_full_cycle
from .notifier import send_summary

logger = logging.getLogger(__name__)

ET = pytz.timezone("America/New_York")

# US stock market holidays 2025-2026 (NYSE)
_MARKET_HOLIDAYS = {
    # 2025
    "2025-01-01", "2025-01-20", "2025-02-17", "2025-04-18",
    "2025-05-26", "2025-06-19", "2025-07-04", "2025-09-01",
    "2025-11-27", "2025-12-25",
    # 2026
    "2026-01-01", "2026-01-19", "2026-02-16", "2026-04-03",
    "2026-05-25", "2026-06-19", "2026-07-03", "2026-09-07",
    "2026-11-26", "2026-12-25",
}


def _is_trading_day(dt: datetime | None = None) -> bool:
    """Return True if today is a NYSE trading day."""
    now = (dt or datetime.now(tz=ET)).date()
    if now.weekday() >= 5:  # Saturday=5, Sunday=6
        return False
    return now.isoformat() not in _MARKET_HOLIDAYS


def _run_job(dry_run: bool = False) -> None:
    """Job executed once per day."""
    now_et = datetime.now(tz=ET)
    if not _is_trading_day(now_et):
        logger.info("Non-trading day (%s) – skipping.", now_et.date())
        return

    logger.info("=== Starting daily Congress Mirror cycle (%s ET) ===", now_et.strftime("%Y-%m-%d %H:%M"))
    try:
        summary = run_full_cycle(dry_run=dry_run)
        send_summary(summary)
        _log_summary(summary)
    except Exception as e:
        logger.error("Unhandled error in daily job: %s", e, exc_info=True)


def _log_summary(s: dict) -> None:
    top = s.get("top_performer") or {}
    logger.info(
        "Top performer: %s  %.1f%%  |  New disclosures: %d  |  Trades placed: %d",
        top.get("name", "–"),
        top.get("return", 0) * 100,
        len(s.get("new_disclosures", [])),
        len(s.get("trades_executed", [])),
    )
    for err in s.get("errors", []):
        logger.warning("  Error: %s", err)


def start(dry_run: bool = False) -> None:
    """Block indefinitely, running the job at 09:30 ET on trading days."""
    # Schedule the job
    schedule.every().day.at("09:30").do(_run_job, dry_run=dry_run)
    logger.info("Scheduler started. Next run at 09:30 ET on the next trading day.")
    logger.info("Press Ctrl+C to stop.")

    # Run immediately on startup so we don't wait until tomorrow
    logger.info("Running initial cycle now …")
    _run_job(dry_run=dry_run)

    while True:
        schedule.run_pending()
        time.sleep(30)
