"""Tests for performance metrics."""

import numpy as np
import pandas as pd
import pytest

from factor_backtester.metrics import (
    annualized_volatility,
    cagr,
    calmar_ratio,
    compare_to_benchmark,
    max_drawdown,
    performance_summary,
    sharpe_ratio,
    win_rate,
)


@pytest.fixture
def constant_growth_curve() -> pd.Series:
    """~1% monthly return for 5 years → known CAGR."""
    dates = pd.bdate_range("2019-01-02", periods=1260, freq="B")
    daily_return = (1.01) ** (1 / 21) - 1
    values = 100_000 * np.cumprod(np.full(1260, 1 + daily_return))
    return pd.Series(values, index=dates)


@pytest.fixture
def flat_curve() -> pd.Series:
    dates = pd.bdate_range("2020-01-01", periods=252, freq="B")
    return pd.Series(100_000.0, index=dates)


class TestCAGR:
    def test_constant_growth(self, constant_growth_curve: pd.Series) -> None:
        result = cagr(constant_growth_curve)
        expected = 1.01**12 - 1
        assert pytest.approx(result, rel=0.05) == expected

    def test_flat_curve_zero(self, flat_curve: pd.Series) -> None:
        assert cagr(flat_curve) == 0.0


class TestVolatility:
    def test_flat_curve_zero_vol(self, flat_curve: pd.Series) -> None:
        assert annualized_volatility(flat_curve) == 0.0

    def test_positive_for_varying_curve(self, constant_growth_curve: pd.Series) -> None:
        assert annualized_volatility(constant_growth_curve) > 0


class TestSharpe:
    def test_flat_curve_zero(self, flat_curve: pd.Series) -> None:
        assert sharpe_ratio(flat_curve) == 0.0

    def test_positive_for_growth(self, constant_growth_curve: pd.Series) -> None:
        assert sharpe_ratio(constant_growth_curve) > 0


class TestMaxDrawdown:
    def test_no_drawdown_on_monotonic_curve(self) -> None:
        dates = pd.bdate_range("2020-01-01", periods=100, freq="B")
        curve = pd.Series(range(100, 200), index=dates, dtype=float)
        assert max_drawdown(curve) == 0.0

    def test_known_drawdown(self) -> None:
        dates = pd.bdate_range("2020-01-01", periods=4, freq="B")
        curve = pd.Series([100.0, 120.0, 90.0, 110.0], index=dates)
        dd = max_drawdown(curve)
        assert pytest.approx(dd, rel=1e-6) == (90.0 - 120.0) / 120.0


class TestCalmar:
    def test_infinite_when_no_drawdown(self) -> None:
        dates = pd.bdate_range("2020-01-01", periods=100, freq="B")
        curve = pd.Series(range(100, 200), index=dates, dtype=float)
        assert calmar_ratio(curve) == float("inf")


class TestWinRate:
    def test_all_positive_months(self, constant_growth_curve: pd.Series) -> None:
        assert win_rate(constant_growth_curve) == 1.0


class TestPerformanceSummary:
    def test_returns_all_keys(self, constant_growth_curve: pd.Series) -> None:
        summary = performance_summary(constant_growth_curve)
        expected_keys = {
            "cagr", "annualized_volatility", "sharpe_ratio", "sortino_ratio",
            "max_drawdown", "max_drawdown_duration_days", "calmar_ratio",
            "win_rate", "best_month", "worst_month",
        }
        assert set(summary.keys()) == expected_keys


class TestCompareToBenchmark:
    def test_returns_two_columns(self, constant_growth_curve: pd.Series) -> None:
        result = compare_to_benchmark(constant_growth_curve, constant_growth_curve)
        assert "strategy" in result.columns
        assert "benchmark" in result.columns
