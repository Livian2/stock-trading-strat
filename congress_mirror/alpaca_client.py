"""Thin wrapper around alpaca-py for paper trading."""
from __future__ import annotations

import logging

from alpaca.trading.client import TradingClient
from alpaca.trading.enums import OrderSide, TimeInForce
from alpaca.trading.requests import MarketOrderRequest

from . import config

logger = logging.getLogger(__name__)


class AlpacaClient:
    def __init__(self) -> None:
        self._client = TradingClient(
            api_key=config.ALPACA_API_KEY,
            secret_key=config.ALPACA_SECRET_KEY,
            paper=True,
        )

    # ------------------------------------------------------------------
    # Account
    # ------------------------------------------------------------------

    def get_account(self) -> dict:
        acct = self._client.get_account()
        return {
            "equity":        float(acct.equity),
            "cash":          float(acct.cash),
            "buying_power":  float(acct.buying_power),
            "portfolio_value": float(acct.portfolio_value),
        }

    def get_buying_power(self) -> float:
        return self.get_account()["buying_power"]

    # ------------------------------------------------------------------
    # Positions
    # ------------------------------------------------------------------

    def get_positions(self) -> list[dict]:
        positions = self._client.get_all_positions()
        return [
            {
                "symbol":      p.symbol,
                "qty":         float(p.qty),
                "market_value": float(p.market_value),
                "avg_entry_price": float(p.avg_entry_price),
                "unrealized_pl": float(p.unrealized_pl),
            }
            for p in positions
        ]

    def liquidate_all(self) -> None:
        """Close all open positions (market orders)."""
        logger.info("Liquidating all positions")
        self._client.close_all_positions(cancel_orders=True)

    def liquidate_symbol(self, symbol: str) -> None:
        logger.info("Liquidating %s", symbol)
        try:
            self._client.close_position(symbol)
        except Exception as e:
            logger.warning("Could not liquidate %s: %s", symbol, e)

    # ------------------------------------------------------------------
    # Orders
    # ------------------------------------------------------------------

    def cancel_all_orders(self) -> None:
        self._client.cancel_orders()

    def place_market_buy(self, symbol: str, notional: float) -> dict | None:
        """Buy `notional` dollars of `symbol`."""
        if notional < 1:
            logger.debug("Skipping buy of %s – notional %.2f too small", symbol, notional)
            return None
        req = MarketOrderRequest(
            symbol=symbol,
            notional=round(notional, 2),
            side=OrderSide.BUY,
            time_in_force=TimeInForce.DAY,
        )
        logger.info("BUY %s $%.2f", symbol, notional)
        try:
            order = self._client.submit_order(req)
            return {"id": str(order.id), "symbol": symbol, "notional": notional, "side": "buy"}
        except Exception as e:
            logger.error("Order failed for %s: %s", symbol, e)
            return None

    def place_market_sell(self, symbol: str, qty: float) -> dict | None:
        """Sell `qty` shares of `symbol`."""
        req = MarketOrderRequest(
            symbol=symbol,
            qty=qty,
            side=OrderSide.SELL,
            time_in_force=TimeInForce.DAY,
        )
        logger.info("SELL %s x%.4f", symbol, qty)
        try:
            order = self._client.submit_order(req)
            return {"id": str(order.id), "symbol": symbol, "qty": qty, "side": "sell"}
        except Exception as e:
            logger.error("Sell order failed for %s: %s", symbol, e)
            return None

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def get_latest_quote(self, symbol: str) -> float | None:
        """Return latest ask/last price for a symbol via Alpaca data."""
        try:
            from alpaca.data.historical import StockHistoricalDataClient
            from alpaca.data.requests import StockLatestQuoteRequest

            data_client = StockHistoricalDataClient(
                api_key=config.ALPACA_API_KEY,
                secret_key=config.ALPACA_SECRET_KEY,
            )
            req = StockLatestQuoteRequest(symbol_or_symbols=symbol)
            quote = data_client.get_stock_latest_quote(req)
            q = quote.get(symbol)
            if q:
                return float(q.ask_price or q.bid_price)
        except Exception as e:
            logger.debug("Alpaca quote failed for %s: %s", symbol, e)
        return None
