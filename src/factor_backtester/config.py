"""Default configuration for the factor backtester."""

from dataclasses import dataclass


@dataclass
class BacktestConfig:
    start: str = "2015-01-01"
    end: str = "2024-12-31"
    top_n: int = 30
    rebalance_freq: str = "M"
    transaction_cost_bps: float = 10.0
    initial_capital: float = 100_000.0


DEFAULT_FACTOR_WEIGHTS: dict[str, float] = {
    "momentum": 0.4,
    "quality": 0.4,
    "value": 0.2,
}

CACHE_DIR = "data/cache"
UNIVERSE_FILE = "universe/sp500.json"
