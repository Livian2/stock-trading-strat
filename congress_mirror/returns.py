"""
Calculate hypothetical 12-month returns for Congress members.

Method
------
For every disclosed BUY in the lookback window:
  1. Fetch the stock's closing price on (or nearest to) the disclosure date.
  2. Compare against today's price.
  3. Weighted average return = Σ(return_i × amount_i) / Σ(amount_i)

Only considers buys that have NOT been matched by a subsequent sell disclosure.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

import yfinance as yf

logger = logging.getLogger(__name__)

_price_cache: dict[tuple[str, str], float] = {}


def _get_price(ticker: str, date: datetime) -> float | None:
    """
    Return the adjusted close price for `ticker` on or just after `date`.
    Cached to avoid redundant network calls.
    """
    date_str = date.strftime("%Y-%m-%d")
    cache_key = (ticker, date_str)
    if cache_key in _price_cache:
        return _price_cache[cache_key]

    try:
        end = date + timedelta(days=7)  # buffer for weekends / holidays
        df = yf.download(
            ticker,
            start=date_str,
            end=end.strftime("%Y-%m-%d"),
            progress=False,
            auto_adjust=True,
        )
        if df.empty:
            return None
        price = float(df["Close"].iloc[0])
        _price_cache[cache_key] = price
        return price
    except Exception as e:
        logger.warning("Price fetch failed for %s on %s: %s", ticker, date_str, e)
        return None


def _get_current_price(ticker: str) -> float | None:
    """Return the most recent closing price for a ticker."""
    cache_key = (ticker, "current")
    if cache_key in _price_cache:
        return _price_cache[cache_key]

    try:
        info = yf.Ticker(ticker).fast_info
        price = float(info.get("last_price") or info.get("regularMarketPrice") or 0)
        if price > 0:
            _price_cache[cache_key] = price
            return price
        # Fallback: last 5 days of history
        df = yf.download(ticker, period="5d", progress=False, auto_adjust=True)
        if not df.empty:
            price = float(df["Close"].iloc[-1])
            _price_cache[cache_key] = price
            return price
    except Exception as e:
        logger.warning("Current price fetch failed for %s: %s", ticker, e)
    return None


def calculate_returns(trades: list[dict]) -> float:
    """
    Given a list of normalised trade dicts for ONE politician,
    return their weighted average return (0.15 = 15%) over the lookback window.

    Trades that could not be priced are silently skipped.
    """
    # Build set of sold tickers to exclude from return calculation
    sold = {t["ticker"] for t in trades if t["type"] == "sell"}

    total_weight = 0.0
    weighted_return = 0.0

    for trade in trades:
        if trade["type"] != "buy":
            continue
        if trade["ticker"] in sold:
            continue  # Position was closed — exclude

        buy_price = _get_price(trade["ticker"], trade["date"])
        current_price = _get_current_price(trade["ticker"])

        if buy_price is None or current_price is None or buy_price == 0:
            logger.debug("Skipping %s – could not price trade on %s", trade["ticker"], trade["date"])
            continue

        ret = (current_price - buy_price) / buy_price
        weight = trade["amount_mid"]
        weighted_return += ret * weight
        total_weight += weight

    if total_weight == 0:
        return 0.0
    return weighted_return / total_weight


def rank_politicians(
    all_trades: dict[str, list[dict]],
    min_trades: int = 5,
) -> list[dict]:
    """
    Rank politicians by 12-month returns.

    Parameters
    ----------
    all_trades : {politician_id: [trade_dicts, ...]}
    min_trades : minimum number of buy trades to be eligible

    Returns
    -------
    List of {id, name, return, trade_count} sorted best-to-worst.
    """
    results = []
    for pol_id, trades in all_trades.items():
        buys = [t for t in trades if t["type"] == "buy"]
        if len(buys) < min_trades:
            continue
        ret = calculate_returns(trades)
        results.append({
            "id":          pol_id,
            "name":        trades[0].get("politician_name", pol_id),
            "return":      ret,
            "trade_count": len(buys),
        })

    results.sort(key=lambda x: x["return"], reverse=True)
    return results


def get_open_positions(trades: list[dict]) -> list[dict]:
    """
    From a politician's trade list, return the positions that are still open
    (bought but not yet disclosed as sold).

    Returns list of {ticker, amount_mid, date} for most-recent buy per ticker.
    """
    # Latest buy per ticker
    buys: dict[str, dict] = {}
    for t in sorted(trades, key=lambda x: x["date"]):
        if t["type"] == "buy":
            buys[t["ticker"]] = t

    # Remove any tickers where a sell appeared after the buy
    sells: dict[str, datetime] = {}
    for t in trades:
        if t["type"] == "sell":
            sells[t["ticker"]] = max(sells.get(t["ticker"], t["date"]), t["date"])

    open_pos = []
    for ticker, buy in buys.items():
        sell_date = sells.get(ticker)
        if sell_date is None or sell_date < buy["date"]:
            open_pos.append(buy)

    return open_pos
