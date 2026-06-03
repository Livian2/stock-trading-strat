"""Price and fundamentals download with Parquet caching."""

from __future__ import annotations

import hashlib
import logging
import time
from pathlib import Path

import pandas as pd
import yfinance as yf

from factor_backtester.config import CACHE_DIR

logger = logging.getLogger(__name__)

_cache_dir = Path(CACHE_DIR)


def _cache_key(prefix: str, tickers: list[str], start: str, end: str) -> str:
    ticker_hash = hashlib.md5("_".join(sorted(tickers)).encode()).hexdigest()[:12]
    return f"{prefix}_{ticker_hash}_{start}_{end}"


def _cache_path(key: str) -> Path:
    _cache_dir.mkdir(parents=True, exist_ok=True)
    return _cache_dir / f"{key}.parquet"


def _is_fresh(path: Path, max_age_hours: float) -> bool:
    if not path.exists():
        return False
    age_hours = (time.time() - path.stat().st_mtime) / 3600
    return age_hours < max_age_hours


def get_prices(tickers: list[str], start: str, end: str) -> pd.DataFrame:
    """Download daily adjusted close prices, with 24h Parquet cache."""
    key = _cache_key("prices", tickers, start, end)
    path = _cache_path(key)

    if _is_fresh(path, max_age_hours=24):
        logger.info("Loading prices from cache: %s", path)
        return pd.read_parquet(path)

    logger.info("Downloading prices for %d tickers...", len(tickers))
    df = yf.download(tickers, start=start, end=end, auto_adjust=True, threads=True)

    if isinstance(df.columns, pd.MultiIndex):
        df = df["Close"]
    elif len(tickers) == 1:
        df = df[["Close"]].rename(columns={"Close": tickers[0]})

    df = df.ffill(limit=5)

    missing = [t for t in tickers if t not in df.columns]
    if missing:
        logger.warning("Missing tickers (skipped): %s", missing[:20])

    df.to_parquet(path)
    logger.info("Prices cached to %s", path)
    return df


def get_fundamentals(tickers: list[str]) -> pd.DataFrame:
    """Download fundamental snapshot for tickers, with 7-day cache."""
    key = _cache_key("fundamentals", tickers, "snapshot", "latest")
    path = _cache_path(key)

    if _is_fresh(path, max_age_hours=168):
        logger.info("Loading fundamentals from cache: %s", path)
        return pd.read_parquet(path)

    logger.info("Downloading fundamentals for %d tickers...", len(tickers))
    rows: list[dict[str, float | str | None]] = []
    for ticker in tickers:
        try:
            info = yf.Ticker(ticker).info
            rows.append({
                "ticker": ticker,
                "roe": info.get("returnOnEquity"),
                "debt_equity": info.get("debtToEquity"),
                "pb": info.get("priceToBook"),
                "pe": info.get("trailingPE"),
                "market_cap": info.get("marketCap"),
            })
        except Exception:
            logger.warning("Failed to fetch fundamentals for %s, skipping", ticker)

    df = pd.DataFrame(rows).set_index("ticker")
    df.to_parquet(path)
    logger.info("Fundamentals cached to %s", path)
    return df
