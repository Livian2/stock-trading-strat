"""Benchmark comparison: SPY and factor ETFs."""

from __future__ import annotations

import pandas as pd

from factor_backtester.data import get_prices
from factor_backtester.metrics import performance_summary

BENCHMARK_ETFS = {
    "SPY": "S&P 500",
    "MTUM": "Momentum (MTUM)",
    "QUAL": "Quality (QUAL)",
    "VLUE": "Value (VLUE)",
}


def get_benchmark_curves(
    start: str, end: str, initial_capital: float = 100_000.0
) -> dict[str, pd.Series]:
    """Download and build buy-and-hold equity curves for benchmark ETFs."""
    tickers = list(BENCHMARK_ETFS.keys())
    prices = get_prices(tickers, start, end)

    curves: dict[str, pd.Series] = {}
    for ticker in tickers:
        if ticker not in prices.columns:
            continue
        series = prices[ticker].dropna()
        if len(series) == 0:
            continue
        curves[ticker] = (series / series.iloc[0]) * initial_capital

    return curves


def benchmark_comparison_table(
    strategy_curve: pd.Series,
    benchmark_curves: dict[str, pd.Series],
) -> pd.DataFrame:
    """Build a metrics comparison table across strategy and benchmarks."""
    all_metrics: dict[str, dict[str, float]] = {
        "Strategy": performance_summary(strategy_curve),
    }
    for ticker, curve in benchmark_curves.items():
        label = BENCHMARK_ETFS.get(ticker, ticker)
        all_metrics[label] = performance_summary(curve)

    return pd.DataFrame(all_metrics)
