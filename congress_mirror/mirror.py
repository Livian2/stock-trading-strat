"""
Core orchestration: fetch → rank → mirror.

Flow
----
1. Download all congressional trades for the lookback window (one bulk fetch).
2. Rank politicians by hypothetical return (buy-and-hold from disclosure → today).
3. Mirror the top performer's open positions into the Alpaca paper account.
4. Return a summary dict for use in email / logging.
"""
from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from pathlib import Path

from . import config
from .alpaca_client import AlpacaClient
from .capitol_trades import clear_cache, get_all_trades
from .returns import get_open_positions, rank_politicians

logger = logging.getLogger(__name__)

STATE_FILE = Path(__file__).parent.parent / "congress_mirror_state.json"


# ---------------------------------------------------------------------------
# State persistence
# ---------------------------------------------------------------------------

def _load_state() -> dict:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text())
        except Exception:
            pass
    return {}


def _save_state(state: dict) -> None:
    STATE_FILE.write_text(json.dumps(state, default=str, indent=2))


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def run_full_cycle(dry_run: bool = False) -> dict:
    """
    Execute one complete scrape → rank → mirror cycle.

    Returns a summary dict with keys:
        top_performer, ranked, new_disclosures, trades_executed, account, errors
    """
    errors: list[str] = []
    state = _load_state()
    summary: dict = {
        "run_at":           datetime.now(tz=UTC).isoformat(),
        "top_performer":    None,
        "ranked":           [],
        "new_disclosures":  [],
        "trades_executed":  [],
        "account":          {},
        "errors":           errors,
    }

    # ------------------------------------------------------------------
    # 1. Fetch all congressional trades for the lookback window (one bulk call)
    # ------------------------------------------------------------------
    clear_cache()  # force fresh data each daily cycle
    logger.info("Fetching congressional trade data …")
    try:
        all_trades = get_all_trades(days=config.LOOKBACK_DAYS)
    except Exception as e:
        msg = f"Failed to fetch trade data: {e}"
        logger.error(msg)
        errors.append(msg)
        return summary

    logger.info(
        "Loaded trades for %d politicians (%d total trades)",
        len(all_trades), sum(len(v) for v in all_trades.values()),
    )
    if not all_trades:
        errors.append("No politician trade data retrieved.")
        return summary

    # ------------------------------------------------------------------
    # 3. Rank by 12-month returns
    # ------------------------------------------------------------------
    ranked = rank_politicians(all_trades, min_trades=config.MIN_TRADES)
    summary["ranked"] = ranked
    if not ranked:
        errors.append("No politicians met the minimum trade count threshold.")
        return summary

    top = ranked[0]
    summary["top_performer"] = top
    logger.info(
        "Top performer: %s  %.1f%% return  (%d trades)",
        top["name"], top["return"] * 100, top["trade_count"],
    )

    # ------------------------------------------------------------------
    # 4. Detect new disclosures since last run
    # ------------------------------------------------------------------
    last_run_str = state.get("last_run")
    last_run = datetime.fromisoformat(last_run_str) if last_run_str else None
    new_disclosures: list[dict] = []
    if last_run:
        for pol_id, trades in all_trades.items():
            for t in trades:
                if t["date"] > last_run:
                    new_disclosures.append(t)
    summary["new_disclosures"] = sorted(
        new_disclosures, key=lambda x: x["date"], reverse=True
    )

    # ------------------------------------------------------------------
    # 5. Mirror top performer's open positions
    # ------------------------------------------------------------------
    open_positions = get_open_positions(all_trades[top["id"]])
    if not open_positions:
        errors.append(f"No open positions found for {top['name']}.")
    else:
        trades_executed = _mirror_positions(
            open_positions, dry_run=dry_run, summary=summary
        )
        summary["trades_executed"] = trades_executed

    # ------------------------------------------------------------------
    # 6. Persist state
    # ------------------------------------------------------------------
    state["last_run"] = summary["run_at"]
    state["top_performer_id"] = top["id"]
    state["top_performer_name"] = top["name"]
    _save_state(state)

    return summary


def _mirror_positions(
    target_positions: list[dict],
    dry_run: bool,
    summary: dict,
) -> list[dict]:
    """
    Reconcile Alpaca paper portfolio with target_positions.

    1. Calculate target weights (proportional to disclosed $ amounts).
    2. Close positions not in target.
    3. Buy positions in target at the right weight.
    """
    client = AlpacaClient()
    account = client.get_account()
    summary["account"] = account

    buying_power = account["buying_power"]
    deploy = buying_power * config.PORTFOLIO_FRACTION

    # Trim to max positions
    positions = sorted(target_positions, key=lambda x: x["amount_mid"], reverse=True)
    positions = positions[: config.MAX_POSITIONS]

    total_disclosed = sum(p["amount_mid"] for p in positions)
    if total_disclosed == 0:
        return []

    # Target notional per ticker
    target: dict[str, float] = {
        p["ticker"]: (p["amount_mid"] / total_disclosed) * deploy
        for p in positions
    }
    target_symbols = set(target)

    trades_executed: list[dict] = []

    # Close positions not in target
    current = client.get_positions()
    current_symbols = {p["symbol"] for p in current}
    for sym in current_symbols - target_symbols:
        if dry_run:
            trades_executed.append({"action": "SELL_ALL", "symbol": sym, "dry_run": True})
            logger.info("[DRY RUN] Would sell all %s", sym)
        else:
            client.liquidate_symbol(sym)
            trades_executed.append({"action": "SELL_ALL", "symbol": sym})

    # Buy / top-up positions in target
    current_value: dict[str, float] = {p["symbol"]: p["market_value"] for p in current}
    for symbol, notional in target.items():
        existing = current_value.get(symbol, 0.0)
        delta = notional - existing
        if delta < 100:  # Don't place tiny orders
            continue
        if dry_run:
            trades_executed.append({"action": "BUY", "symbol": symbol, "notional": round(delta, 2), "dry_run": True})
            logger.info("[DRY RUN] Would buy %s $%.2f", symbol, delta)
        else:
            result = client.place_market_buy(symbol, delta)
            if result:
                trades_executed.append(result)

    return trades_executed
