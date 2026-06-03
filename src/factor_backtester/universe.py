"""Universe definition — S&P 500 tickers."""

import json
from pathlib import Path

from factor_backtester.config import UNIVERSE_FILE


def load_sp500() -> list[str]:
    """Load S&P 500 tickers from the committed JSON file."""
    path = Path(UNIVERSE_FILE)
    if not path.exists():
        path = Path(__file__).resolve().parents[2] / UNIVERSE_FILE
    with open(path) as f:
        tickers: list[str] = json.load(f)
    return tickers
