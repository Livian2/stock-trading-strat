"""Streamlit dashboard for the factor backtester."""

from __future__ import annotations

import sys
from pathlib import Path

import plotly.graph_objects as go
import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from factor_backtester.backtest import BacktestConfig, run_backtest  # noqa: E402
from factor_backtester.benchmark import (  # noqa: E402
    BENCHMARK_ETFS,
    benchmark_comparison_table,
    get_benchmark_curves,
)
from factor_backtester.data import get_fundamentals, get_prices  # noqa: E402
from factor_backtester.factors import (  # noqa: E402
    composite_score,
    momentum,
    quality,
    value,
)
from factor_backtester.universe import load_sp500  # noqa: E402

st.set_page_config(page_title="Factor Backtester", layout="wide")
st.title("Multi-Factor Equity Backtester")

st.sidebar.header("Parameters")
start_date = st.sidebar.date_input("Start date", value=None) or "2018-01-01"
end_date = st.sidebar.date_input("End date", value=None) or "2024-12-31"
start_str = str(start_date)
end_str = str(end_date)

factor_choice = st.sidebar.selectbox(
    "Factor", ["Composite", "Momentum", "Quality", "Value"]
)

top_n = st.sidebar.slider("Top N stocks", 10, 100, 30)
cost_bps = st.sidebar.slider("Transaction cost (bps)", 0, 50, 10)

if factor_choice == "Composite":
    st.sidebar.subheader("Factor weights")
    w_mom = st.sidebar.slider("Momentum weight", 0.0, 1.0, 0.4, 0.05)
    w_qual = st.sidebar.slider("Quality weight", 0.0, 1.0, 0.4, 0.05)
    w_val = st.sidebar.slider("Value weight", 0.0, 1.0, 0.2, 0.05)
    factor_weights = {"momentum": w_mom, "quality": w_qual, "value": w_val}

log_scale = st.sidebar.checkbox("Log scale", value=False)

st.sidebar.markdown("---")
st.sidebar.markdown(
    "**Caveat:** Uses current S&P 500 constituents — results have "
    "survivorship bias. See CAVEATS.md for full list of limitations."
)

run_button = st.sidebar.button("Run Backtest", type="primary")

if run_button:
    with st.spinner("Loading universe and data..."):
        tickers = load_sp500()
        prices = get_prices(tickers, start_str, end_str)
        fundamentals = get_fundamentals(list(prices.columns))

    config = BacktestConfig(
        start=start_str,
        end=end_str,
        top_n=top_n,
        transaction_cost_bps=cost_bps,
    )

    def make_score_fn(choice: str):
        def score_fn(as_of):
            if choice == "Momentum":
                return momentum(prices, as_of)
            elif choice == "Quality":
                return quality(fundamentals, as_of)
            elif choice == "Value":
                return value(fundamentals, as_of)
            else:
                scores = {
                    "momentum": momentum(prices, as_of),
                    "quality": quality(fundamentals, as_of),
                    "value": value(fundamentals, as_of),
                }
                return composite_score(scores, factor_weights)
        return score_fn

    with st.spinner("Running backtest..."):
        result = run_backtest(prices, make_score_fn(factor_choice), config)

    with st.spinner("Loading benchmarks..."):
        bench_curves = get_benchmark_curves(
            start_str, end_str, config.initial_capital
        )

    # --- Equity Curve ---
    st.subheader("Equity Curve")
    fig_eq = go.Figure()
    fig_eq.add_trace(go.Scatter(
        x=result.equity_curve.index,
        y=result.equity_curve.values,
        name=f"Strategy ({factor_choice})",
        line=dict(width=2),
    ))
    for ticker, curve in bench_curves.items():
        label = BENCHMARK_ETFS.get(ticker, ticker)
        fig_eq.add_trace(go.Scatter(
            x=curve.index, y=curve.values,
            name=label, line=dict(width=1, dash="dash"),
        ))
    if log_scale:
        fig_eq.update_yaxes(type="log")
    fig_eq.update_layout(
        height=500, xaxis_title="Date",
        yaxis_title="Portfolio Value ($)",
    )
    st.plotly_chart(fig_eq, use_container_width=True)

    # --- Drawdown Chart ---
    st.subheader("Drawdown")
    cummax = result.equity_curve.cummax()
    dd = (result.equity_curve - cummax) / cummax
    fig_dd = go.Figure()
    fig_dd.add_trace(go.Scatter(
        x=dd.index, y=dd.values, fill="tozeroy",
        name="Drawdown", line=dict(color="red", width=1),
    ))
    fig_dd.update_layout(
        height=300, yaxis_title="Drawdown",
        yaxis_tickformat=".0%",
    )
    st.plotly_chart(fig_dd, use_container_width=True)

    # --- Rolling 12-month return ---
    st.subheader("Rolling 12-Month Return")
    rolling_ret = result.equity_curve.pct_change(252).dropna()
    fig_roll = go.Figure()
    fig_roll.add_trace(go.Scatter(
        x=rolling_ret.index, y=rolling_ret.values,
        name="Rolling 12M Return", line=dict(width=1),
    ))
    fig_roll.update_layout(height=300, yaxis_tickformat=".0%")
    st.plotly_chart(fig_roll, use_container_width=True)

    # --- Metrics Table ---
    st.subheader("Performance Metrics")
    comp_table = benchmark_comparison_table(
        result.equity_curve, bench_curves
    )
    comp_table.loc["total_transaction_costs"] = 0.0
    comp_table.at[
        "total_transaction_costs", "Strategy"
    ] = result.costs_paid

    display = comp_table.astype(str).copy()
    fmt_pct = [
        "cagr", "annualized_volatility", "max_drawdown",
        "win_rate", "best_month", "worst_month",
    ]
    for row in fmt_pct:
        if row in comp_table.index:
            for col in comp_table.columns:
                v = float(comp_table.at[row, col])
                display.at[row, col] = f"{v:.2%}"
    for row in ["sharpe_ratio", "sortino_ratio", "calmar_ratio"]:
        if row in comp_table.index:
            for col in comp_table.columns:
                v = float(comp_table.at[row, col])
                display.at[row, col] = f"{v:.2f}"
    if "max_drawdown_duration_days" in comp_table.index:
        for col in comp_table.columns:
            v = comp_table.at["max_drawdown_duration_days", col]
            display.at["max_drawdown_duration_days", col] = (
                f"{int(float(v))}d"
            )
    if "total_transaction_costs" in comp_table.index:
        for col in comp_table.columns:
            v = comp_table.at["total_transaction_costs", col]
            display.at["total_transaction_costs", col] = (
                f"${float(v):,.0f}"
            )

    st.dataframe(display, use_container_width=True)

    # --- Current Holdings ---
    st.subheader("Current Holdings (Latest Rebalance)")
    if not result.holdings.empty:
        last = result.holdings.iloc[-1].dropna()
        last = last.sort_values(ascending=False)
        st.dataframe(
            last.reset_index().rename(
                columns={"index": "Ticker", last.name: "Weight"}
            ),
            use_container_width=True,
        )
    else:
        st.write("No holdings data available.")

    # --- Turnover ---
    st.subheader("Monthly Turnover")
    if len(result.turnover) > 0:
        fig_turn = go.Figure()
        fig_turn.add_trace(go.Bar(
            x=result.turnover.index,
            y=result.turnover.values,
            name="Turnover",
        ))
        fig_turn.update_layout(height=300, yaxis_tickformat=".0%")
        st.plotly_chart(fig_turn, use_container_width=True)
        st.metric(
            "Average Turnover", f"{result.turnover.mean():.1%}"
        )

    st.metric(
        "Total Transaction Costs", f"${result.costs_paid:,.2f}"
    )
