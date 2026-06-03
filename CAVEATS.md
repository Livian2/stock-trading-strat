# Caveats and Limitations

This is an educational backtester. The results are not suitable for making real investment decisions. The following limitations apply:

## Survivorship Bias

The backtest uses the **current** S&P 500 constituent list as the historical universe. Companies that were removed from the index due to poor performance, bankruptcy, or acquisition are excluded. This systematically overstates historical returns by an estimated 1-3% per year.

## Look-Ahead Bias in Fundamentals

yfinance provides a **current snapshot** of fundamental data (ROE, P/B, P/E), not point-in-time values. The backtester uses these values as if they were available at each historical rebalance date. In reality, financial statements are published with a lag.

## No Short Selling or Leverage

The strategy is long-only, equal-weight. Real factor strategies often go long high-score and short low-score stocks (long-short portfolios). The long-only constraint reduces factor exposure.

## Simplified Transaction Cost Model

Costs are modeled as a flat basis-point charge on turnover. This ignores:
- Bid-ask spreads (vary by stock liquidity)
- Market impact (large orders move prices)
- Timing of execution within the trading day

## No Walk-Forward Validation

Factor definitions are fixed from academic literature. No out-of-sample or walk-forward validation is performed. Any parameter that was tuned (even implicitly) may be overfit to the backtest period.

## Single Rebalance Frequency

Monthly rebalancing only. The choice of rebalance frequency affects returns, turnover, and costs. No analysis of alternative frequencies is included.

## Data Quality

- yfinance data may contain errors or gaps
- Adjusted close prices depend on yfinance's adjustment methodology
- Small number of tickers may fail to download and are silently skipped

## Past Performance

Past performance does not predict future returns. Factor premia may shrink or disappear as more capital chases them.
