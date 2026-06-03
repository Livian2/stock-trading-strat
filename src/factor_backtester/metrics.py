"""Performance metrics for equity curves."""

from __future__ import annotations

import numpy as np
import pandas as pd


def _annualization_factor(equity_curve: pd.Series) -> float:
    """Estimate trading days per year from the index."""
    if len(equity_curve) < 2:
        return 252
    days = (equity_curve.index[-1] - equity_curve.index[0]).days
    return len(equity_curve) / (days / 365.25)


def cagr(equity_curve: pd.Series) -> float:
    if len(equity_curve) < 2:
        return 0.0
    total_return = equity_curve.iloc[-1] / equity_curve.iloc[0]
    days = (equity_curve.index[-1] - equity_curve.index[0]).days
    if days <= 0:
        return 0.0
    return float(total_return ** (365.25 / days) - 1)


def annualized_volatility(equity_curve: pd.Series) -> float:
    returns = equity_curve.pct_change().dropna()
    if len(returns) < 2:
        return 0.0
    ann_factor = _annualization_factor(equity_curve)
    return float(returns.std() * np.sqrt(ann_factor))


def sharpe_ratio(equity_curve: pd.Series, risk_free: float = 0.0) -> float:
    vol = annualized_volatility(equity_curve)
    if vol == 0:
        return 0.0
    return (cagr(equity_curve) - risk_free) / vol


def sortino_ratio(equity_curve: pd.Series, risk_free: float = 0.0) -> float:
    returns = equity_curve.pct_change().dropna()
    if len(returns) < 2:
        return 0.0
    ann_factor = _annualization_factor(equity_curve)
    downside = returns[returns < 0]
    if len(downside) == 0:
        return float("inf")
    downside_vol = float(downside.std() * np.sqrt(ann_factor))
    if downside_vol == 0:
        return 0.0
    return (cagr(equity_curve) - risk_free) / downside_vol


def max_drawdown(equity_curve: pd.Series) -> float:
    cummax = equity_curve.cummax()
    drawdown = (equity_curve - cummax) / cummax
    return float(drawdown.min())


def max_drawdown_duration(equity_curve: pd.Series) -> int:
    """Max drawdown duration in calendar days."""
    cummax = equity_curve.cummax()
    in_drawdown = equity_curve < cummax
    if not in_drawdown.any():
        return 0

    max_dur = 0
    start = None
    for date, is_dd in in_drawdown.items():
        if is_dd:
            if start is None:
                start = date
        else:
            if start is not None:
                dur = (date - start).days
                max_dur = max(max_dur, dur)
                start = None
    if start is not None:
        dur = (equity_curve.index[-1] - start).days
        max_dur = max(max_dur, dur)
    return max_dur


def calmar_ratio(equity_curve: pd.Series) -> float:
    dd = max_drawdown(equity_curve)
    if dd == 0:
        return float("inf")
    return cagr(equity_curve) / abs(dd)


def win_rate(equity_curve: pd.Series) -> float:
    """Percentage of months with positive return."""
    monthly = equity_curve.resample("ME").last()
    monthly_returns = monthly.pct_change().dropna()
    if len(monthly_returns) == 0:
        return 0.0
    return float((monthly_returns > 0).sum() / len(monthly_returns))


def best_month(equity_curve: pd.Series) -> float:
    monthly = equity_curve.resample("ME").last()
    monthly_returns = monthly.pct_change().dropna()
    return float(monthly_returns.max()) if len(monthly_returns) > 0 else 0.0


def worst_month(equity_curve: pd.Series) -> float:
    monthly = equity_curve.resample("ME").last()
    monthly_returns = monthly.pct_change().dropna()
    return float(monthly_returns.min()) if len(monthly_returns) > 0 else 0.0


def performance_summary(equity_curve: pd.Series) -> dict[str, float]:
    return {
        "cagr": cagr(equity_curve),
        "annualized_volatility": annualized_volatility(equity_curve),
        "sharpe_ratio": sharpe_ratio(equity_curve),
        "sortino_ratio": sortino_ratio(equity_curve),
        "max_drawdown": max_drawdown(equity_curve),
        "max_drawdown_duration_days": max_drawdown_duration(equity_curve),
        "calmar_ratio": calmar_ratio(equity_curve),
        "win_rate": win_rate(equity_curve),
        "best_month": best_month(equity_curve),
        "worst_month": worst_month(equity_curve),
    }


def compare_to_benchmark(
    strategy_curve: pd.Series, benchmark_curve: pd.Series
) -> pd.DataFrame:
    strat_metrics = performance_summary(strategy_curve)
    bench_metrics = performance_summary(benchmark_curve)
    return pd.DataFrame({"strategy": strat_metrics, "benchmark": bench_metrics})
