"""Factor scoring: momentum, quality, value, and composite."""

from __future__ import annotations

import pandas as pd


def momentum(prices: pd.DataFrame, as_of: pd.Timestamp) -> pd.Series:
    """12-1 momentum: return from t-12m to t-1m (skip most recent month)."""
    end = as_of - pd.DateOffset(months=1)
    start = as_of - pd.DateOffset(months=12)

    prices_before_end = prices.loc[:end]
    prices_before_start = prices.loc[:start]

    if prices_before_end.empty or prices_before_start.empty:
        return pd.Series(dtype=float)

    p_end = prices_before_end.iloc[-1]
    p_start = prices_before_start.iloc[-1]

    score = p_end / p_start - 1
    return score.dropna()


def quality(fundamentals: pd.DataFrame, as_of: pd.Timestamp) -> pd.Series:
    """Quality score: trailing ROE."""
    _ = as_of
    if "roe" not in fundamentals.columns:
        return pd.Series(dtype=float)
    return fundamentals["roe"].dropna()


def value(fundamentals: pd.DataFrame, as_of: pd.Timestamp) -> pd.Series:
    """Value score: inverse price-to-book."""
    _ = as_of
    if "pb" not in fundamentals.columns:
        return pd.Series(dtype=float)
    pb = fundamentals["pb"].dropna()
    pb = pb[pb > 0]
    return 1.0 / pb


def composite_score(
    scores: dict[str, pd.Series], weights: dict[str, float]
) -> pd.Series:
    """Combine factor scores by ranking each independently then weighting ranks."""
    all_tickers = set()
    for s in scores.values():
        all_tickers.update(s.index)

    ranked: dict[str, pd.Series] = {}
    for name, s in scores.items():
        ranked[name] = s.rank(pct=True)

    combined = pd.Series(0.0, index=list(all_tickers))
    total_weight = sum(weights.get(name, 0) for name in scores)

    for name, rank_series in ranked.items():
        w = weights.get(name, 0) / total_weight if total_weight > 0 else 0
        combined = combined.add(rank_series * w, fill_value=0)

    return combined.dropna()
