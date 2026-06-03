"""Backtesting engine: monthly-rebalanced, equal-weight, top-N."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

import numpy as np
import pandas as pd

from factor_backtester.config import BacktestConfig


@dataclass
class BacktestResult:
    equity_curve: pd.Series
    holdings: pd.DataFrame
    turnover: pd.Series
    costs_paid: float
    config: BacktestConfig


def _get_rebalance_dates(prices: pd.DataFrame, start: str, end: str) -> list[pd.Timestamp]:
    """First trading day of each month in the date range."""
    mask = (prices.index >= pd.Timestamp(start)) & (prices.index <= pd.Timestamp(end))
    dates = prices.index[mask]
    monthly = dates.to_period("M").unique()
    rebal_dates: list[pd.Timestamp] = []
    for period in monthly:
        month_dates = dates[(dates >= period.start_time) & (dates <= period.end_time)]
        if len(month_dates) > 0:
            rebal_dates.append(month_dates[0])
    return rebal_dates


def run_backtest(
    prices: pd.DataFrame,
    score_fn: Callable[[pd.Timestamp], pd.Series],
    config: BacktestConfig,
) -> BacktestResult:
    """Run a monthly-rebalanced backtest.

    score_fn(as_of) must return a Series indexed by ticker. Only data before as_of
    should be used (no look-ahead). Higher score = more desirable.
    """
    rebalance_dates = _get_rebalance_dates(prices, config.start, config.end)
    if len(rebalance_dates) < 2:
        raise ValueError("Not enough rebalance dates in the given range")

    trading_dates = prices.loc[config.start:config.end].index
    equity = pd.Series(index=trading_dates, dtype=float)
    holdings_records: list[dict] = []
    turnover_records: list[tuple[pd.Timestamp, float]] = []

    capital = config.initial_capital
    current_weights: dict[str, float] = {}
    total_costs = 0.0

    rebal_set = set(rebalance_dates)
    rebal_idx = 0

    for i, date in enumerate(trading_dates):
        if date in rebal_set and rebal_idx < len(rebalance_dates):
            scores = score_fn(date)
            if len(scores) == 0:
                equity.iloc[i] = capital
                continue

            valid_tickers = [t for t in scores.index if t in prices.columns]
            scores = scores[valid_tickers].dropna()

            top = scores.nlargest(config.top_n)
            new_weights = {t: 1.0 / len(top) for t in top.index}

            turnover = 0.0
            all_tickers = set(current_weights) | set(new_weights)
            for t in all_tickers:
                turnover += abs(new_weights.get(t, 0) - current_weights.get(t, 0))
            turnover /= 2

            cost = turnover * capital * (config.transaction_cost_bps / 10_000)
            total_costs += cost
            capital -= cost

            current_weights = new_weights
            turnover_records.append((date, turnover))
            holdings_records.append({"date": date, **{t: w for t, w in new_weights.items()}})
            rebal_idx += 1

        if i > 0 and current_weights:
            prev_date = trading_dates[i - 1]
            daily_returns = prices.loc[date] / prices.loc[prev_date] - 1
            port_return = sum(
                current_weights.get(t, 0) * daily_returns.get(t, 0)
                for t in current_weights
                if not np.isnan(daily_returns.get(t, 0))
            )
            capital *= 1 + port_return

        equity.iloc[i] = capital

    holdings_df = pd.DataFrame(holdings_records)
    if not holdings_df.empty and "date" in holdings_df.columns:
        holdings_df = holdings_df.set_index("date")

    turnover_series = pd.Series(
        dict(turnover_records), name="turnover", dtype=float
    ) if turnover_records else pd.Series(dtype=float, name="turnover")

    return BacktestResult(
        equity_curve=equity.dropna(),
        holdings=holdings_df,
        turnover=turnover_series,
        costs_paid=total_costs,
        config=config,
    )
