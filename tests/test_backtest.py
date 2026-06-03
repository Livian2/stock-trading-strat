"""Tests for the backtesting engine."""

import numpy as np
import pandas as pd
import pytest

from factor_backtester.backtest import BacktestConfig, run_backtest


@pytest.fixture
def simple_prices() -> pd.DataFrame:
    """3 stocks, ~2 years of daily data with known returns."""
    dates = pd.bdate_range("2020-01-01", "2021-12-31", freq="B")
    np.random.seed(42)
    n = len(dates)
    return pd.DataFrame(
        {
            "A": 100 * np.cumprod(1 + np.random.normal(0.0005, 0.01, n)),
            "B": 100 * np.cumprod(1 + np.random.normal(0.0003, 0.015, n)),
            "C": 100 * np.cumprod(1 + np.random.normal(-0.0002, 0.02, n)),
        },
        index=dates,
    )


class TestRunBacktest:
    def test_equity_curve_has_no_nans(self, simple_prices: pd.DataFrame) -> None:
        config = BacktestConfig(start="2020-03-01", end="2021-12-31", top_n=2)

        def score_fn(as_of: pd.Timestamp) -> pd.Series:
            return pd.Series({"A": 3, "B": 2, "C": 1})

        result = run_backtest(simple_prices, score_fn, config)
        assert result.equity_curve.isna().sum() == 0

    def test_perfect_foresight_beats_market(self, simple_prices: pd.DataFrame) -> None:
        """A cheating score function should produce strong returns."""
        config = BacktestConfig(
            start="2020-03-01", end="2021-12-31", top_n=1, transaction_cost_bps=0
        )

        def foresight_score(as_of: pd.Timestamp) -> pd.Series:
            future = simple_prices.loc[as_of:].iloc[1:22]
            if future.empty:
                return pd.Series(dtype=float)
            return (future.iloc[-1] / future.iloc[0] - 1).dropna()

        result = run_backtest(simple_prices, foresight_score, config)
        total_return = result.equity_curve.iloc[-1] / result.equity_curve.iloc[0] - 1
        assert total_return > 0.3

    def test_transaction_costs_reduce_returns(self, simple_prices: pd.DataFrame) -> None:
        def score_fn(as_of: pd.Timestamp) -> pd.Series:
            return pd.Series({"A": 3, "B": 2, "C": 1})

        config_no_cost = BacktestConfig(
            start="2020-03-01", end="2021-12-31", top_n=2, transaction_cost_bps=0
        )
        config_with_cost = BacktestConfig(
            start="2020-03-01", end="2021-12-31", top_n=2, transaction_cost_bps=50
        )

        result_no_cost = run_backtest(simple_prices, score_fn, config_no_cost)
        result_with_cost = run_backtest(simple_prices, score_fn, config_with_cost)

        assert result_no_cost.equity_curve.iloc[-1] >= result_with_cost.equity_curve.iloc[-1]
        assert result_with_cost.costs_paid > 0

    def test_reproducible(self, simple_prices: pd.DataFrame) -> None:
        config = BacktestConfig(start="2020-03-01", end="2021-12-31", top_n=2)

        def score_fn(as_of: pd.Timestamp) -> pd.Series:
            return pd.Series({"A": 3, "B": 2, "C": 1})

        r1 = run_backtest(simple_prices, score_fn, config)
        r2 = run_backtest(simple_prices, score_fn, config)
        pd.testing.assert_series_equal(r1.equity_curve, r2.equity_curve)
