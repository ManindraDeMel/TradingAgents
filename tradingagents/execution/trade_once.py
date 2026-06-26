from __future__ import annotations

import argparse
import logging

from . import position_policy
from .broker.base import Broker, OrderIntent
from .config import ExecutionConfig

logger = logging.getLogger(__name__)


def _default_price(symbol: str) -> float:
    import yfinance as yf

    from tradingagents.dataflows.symbol_utils import normalize_symbol

    hist = yf.Ticker(normalize_symbol(symbol)).history(period="1d")
    return float(hist["Close"].iloc[-1])


def _default_broker(cfg: ExecutionConfig) -> Broker:
    import os

    from .broker.alpaca import AlpacaBroker

    return AlpacaBroker(
        api_key=os.environ.get("ALPACA_API_KEY"),
        secret_key=os.environ.get("ALPACA_SECRET_KEY"),
        paper=not cfg.allow_live,
        allow_live=cfg.allow_live,
    )


def _default_rating(ticker: str, date: str) -> str:
    from tradingagents.default_config import DEFAULT_CONFIG
    from tradingagents.graph.trading_graph import TradingAgentsGraph

    graph = TradingAgentsGraph(config=DEFAULT_CONFIG.copy())
    _, rating = graph.propagate(ticker, date)
    return rating


def run_trade_once(
    ticker: str,
    date: str,
    *,
    cfg: ExecutionConfig | None = None,
    broker: Broker | None = None,
    rating_fn=None,
    price_fn=None,
    cycle_id: str | None = None,
) -> list[OrderIntent]:
    """Run one agent analysis for ``ticker`` and place the matching paper order(s)."""
    cfg = cfg or ExecutionConfig.from_env()
    broker = broker or _default_broker(cfg)
    rating_fn = rating_fn or _default_rating
    price_fn = price_fn or _default_price
    cycle_id = cycle_id or f"{date}#1"

    rating = rating_fn(ticker, date)
    price = price_fn(ticker)
    account = broker.account()
    position = broker.get_position(ticker)

    intents = position_policy.decide(ticker, rating, position, account, price, cfg, cycle_id)
    for intent in intents:
        order_id = broker.submit_order(intent)
        logger.info(
            "Submitted %s %s x%d (%s) -> %s",
            intent.side, intent.symbol, intent.qty, intent.client_order_id, order_id,
        )
    if not intents:
        logger.info("No order for %s (rating=%s): target matches current position.", ticker, rating)
    return intents


def main(argv: list[str] | None = None) -> None:
    import datetime

    parser = argparse.ArgumentParser(
        prog="trade-once",
        description="Run one agent analysis and place the matching Alpaca paper order.",
    )
    parser.add_argument("ticker")
    parser.add_argument("--date", default=datetime.datetime.now().strftime("%Y-%m-%d"))
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    run_trade_once(args.ticker, args.date)


if __name__ == "__main__":
    main()
