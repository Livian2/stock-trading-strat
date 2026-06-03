# Factor Backtester

Multi-asset equity factor backtester that builds a DIY systematic factor portfolio and compares it against benchmark ETFs (SPY, MTUM, QUAL, VLUE).

## Factors

- **Momentum (12-1):** Jegadeesh-Titman convention — trailing 12-month return skipping the most recent month
- **Quality:** Trailing return on equity (ROE)
- **Value:** Inverse price-to-book (1/P/B)
- **Composite:** Rank-weighted combination of all three

## Install

```bash
# Requires Python 3.11+
uv sync            # or: pip install -e ".[dev]"
```

## Run Tests

```bash
pytest
```

## Run Dashboard

```bash
streamlit run dashboard/app.py
```

The dashboard lets you select factors, date ranges, top-N holdings, and transaction costs. Click "Run Backtest" to see equity curves, drawdowns, rolling returns, and a full metrics comparison table.

## Methodology

The backtester runs a monthly-rebalanced, equal-weight, top-N strategy:

1. On the first trading day of each month, compute factor scores using only data available before that date (no look-ahead)
2. Rank stocks and select the top N (default 30)
3. Rebalance to equal weight, applying transaction costs on turnover
4. Hold until the next rebalance; portfolio value drifts with daily returns

## Honest Results

The realistic expected outcome is performance **comparable to or slightly worse than** the published factor ETFs after costs. If results look dramatically better, suspect a bug before celebrating.

Key biases that inflate backtested returns:
- **Survivorship bias:** Uses current S&P 500 constituents, missing companies that were delisted or removed. This alone can overstate returns by 1-3% per year.
- **Fundamental data limitations:** yfinance provides current snapshots, not point-in-time data.
- **Simplified cost model:** Flat bps on turnover; ignores market impact, bid-ask spreads, and slippage.

See [CAVEATS.md](CAVEATS.md) for the full list of limitations.

## License

MIT
