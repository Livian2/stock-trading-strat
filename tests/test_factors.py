"""Tests for factor scoring functions."""

import pandas as pd
import pytest

from factor_backtester.factors import composite_score, momentum, quality, value


@pytest.fixture
def sample_prices() -> pd.DataFrame:
    dates = pd.bdate_range("2020-01-01", periods=300, freq="B")
    return pd.DataFrame(
        {
            "AAPL": [100 * (1.001**i) for i in range(300)],
            "MSFT": [200 * (1.0005**i) for i in range(300)],
            "GOOG": [150 * (0.999**i) for i in range(300)],
        },
        index=dates,
    )


@pytest.fixture
def sample_fundamentals() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "roe": [0.25, 0.18, 0.30],
            "pb": [5.0, 3.0, 8.0],
            "pe": [20.0, 15.0, 30.0],
        },
        index=["AAPL", "MSFT", "GOOG"],
    )


class TestMomentum:
    def test_returns_scores_for_valid_tickers(self, sample_prices: pd.DataFrame) -> None:
        as_of = sample_prices.index[-1]
        scores = momentum(sample_prices, as_of)
        assert len(scores) > 0
        assert "AAPL" in scores.index

    def test_positive_momentum_for_rising_stock(self, sample_prices: pd.DataFrame) -> None:
        as_of = sample_prices.index[-1]
        scores = momentum(sample_prices, as_of)
        assert scores["AAPL"] > 0

    def test_negative_momentum_for_falling_stock(self, sample_prices: pd.DataFrame) -> None:
        as_of = sample_prices.index[-1]
        scores = momentum(sample_prices, as_of)
        assert scores["GOOG"] < 0

    def test_insufficient_history_returns_empty(self) -> None:
        dates = pd.bdate_range("2020-01-01", periods=10, freq="B")
        prices = pd.DataFrame({"AAPL": range(10)}, index=dates)
        scores = momentum(prices, dates[-1])
        assert len(scores) == 0


class TestQuality:
    def test_returns_roe_values(self, sample_fundamentals: pd.DataFrame) -> None:
        scores = quality(sample_fundamentals, pd.Timestamp("2024-01-01"))
        assert scores["AAPL"] == 0.25
        assert scores["GOOG"] == 0.30

    def test_handles_missing_roe(self) -> None:
        df = pd.DataFrame({"pb": [5.0]}, index=["AAPL"])
        scores = quality(df, pd.Timestamp("2024-01-01"))
        assert len(scores) == 0


class TestValue:
    def test_returns_inverse_pb(self, sample_fundamentals: pd.DataFrame) -> None:
        scores = value(sample_fundamentals, pd.Timestamp("2024-01-01"))
        assert pytest.approx(scores["AAPL"], rel=1e-6) == 1.0 / 5.0
        assert pytest.approx(scores["MSFT"], rel=1e-6) == 1.0 / 3.0

    def test_excludes_zero_pb(self) -> None:
        df = pd.DataFrame({"pb": [0.0, 5.0]}, index=["BAD", "GOOD"])
        scores = value(df, pd.Timestamp("2024-01-01"))
        assert "BAD" not in scores.index
        assert "GOOD" in scores.index


class TestComposite:
    def test_combines_scores_with_weights(self) -> None:
        scores = {
            "a": pd.Series({"X": 10, "Y": 20, "Z": 30}),
            "b": pd.Series({"X": 30, "Y": 20, "Z": 10}),
        }
        weights = {"a": 0.5, "b": 0.5}
        result = composite_score(scores, weights)
        assert len(result) == 3
        # Equal weights on inversely correlated factors → similar composite
        assert abs(result["X"] - result["Z"]) < 0.5

    def test_single_factor_preserves_ranking(self) -> None:
        scores = {"a": pd.Series({"X": 10, "Y": 20, "Z": 30})}
        weights = {"a": 1.0}
        result = composite_score(scores, weights)
        assert result["Z"] > result["Y"] > result["X"]
