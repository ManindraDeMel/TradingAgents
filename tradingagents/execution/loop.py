from __future__ import annotations

import argparse
import logging

from . import position_policy, risk
from .broker.base import Broker
from .config import ExecutionConfig
from .ledger import Ledger

logger = logging.getLogger(__name__)


def run_cycle(
    date: str,
    *,
    cfg: ExecutionConfig,
    broker: Broker,
    rating_fn,
    screen_fn,
    price_fn,
    calendar_fn=None,
    baseline_equity: float | None = None,
    ledger: Ledger | None = None,
    cycle_id: str | None = None,
) -> dict:
    """Run one autonomous trading cycle: screen -> re-evaluate -> risk -> submit."""
    cycle_id = cycle_id or f"{date}#1"
    if calendar_fn is not None and not calendar_fn(date):
        logger.info("Market closed on %s; nothing to do.", date)
        return {"status": "market_closed", "tickers": 0, "intents": 0, "orders": 0,
                "kill_switch": False}

    account = broker.account()
    positions = broker.positions()
    held = {p.symbol for p in positions}
    gross_exposure = sum(abs(p.market_value) for p in positions)
    baseline = baseline_equity if baseline_equity is not None else account.equity
    if ledger is not None:
        ledger.snapshot_equity(account.equity, date)

    tripped = risk.kill_switch_tripped(baseline, account.equity, cfg)
    if tripped:
        logger.warning(
            "Daily-loss kill switch tripped (baseline=%.2f current=%.2f); halting new entries.",
            baseline, account.equity,
        )

    # Always re-evaluate held names, even when the screen doesn't surface them.
    tickers = list(dict.fromkeys(list(screen_fn()) + sorted(held)))

    all_intents = []
    prices: dict[str, float] = {}
    for ticker in tickers:
        rating = rating_fn(ticker, date)
        price = price_fn(ticker)
        prices[ticker] = price
        position = broker.get_position(ticker)
        all_intents.extend(
            position_policy.decide(ticker, rating, position, account, price, cfg, cycle_id)
        )

    allowed = risk.apply(
        all_intents,
        held_symbols=held,
        cfg=cfg,
        kill_switch=tripped,
        gross_exposure=gross_exposure,
        equity=account.equity,
        notional_fn=lambda intent: intent.qty * prices[intent.symbol],
    )
    orders = 0
    for intent in allowed:
        order_id = broker.submit_order(intent)
        if ledger is not None:
            ledger.record_order(intent, order_id)
        orders += 1

    summary = {
        "status": "ok",
        "tickers": len(tickers),
        "intents": len(all_intents),
        "orders": orders,
        "kill_switch": tripped,
    }
    if ledger is not None:
        ledger.write_cycle_summary(date, summary)
    logger.info(
        "Cycle %s: %d tickers, %d intents, %d orders%s",
        date, len(tickers), len(all_intents), orders, " (kill switch)" if tripped else "",
    )
    return summary


def main(argv: list[str] | None = None) -> None:
    import datetime
    import os

    from tradingagents.dataflows.symbol_utils import normalize_symbol

    from .screener import rank_by_volatility
    from .trade_once import _default_broker, _default_price, _default_rating

    parser = argparse.ArgumentParser(
        prog="trading-loop",
        description="Run one autonomous trading cycle: screen by volatility, re-evaluate, place paper orders.",
    )
    parser.add_argument("--tickers", required=True,
                        help="Comma-separated candidate universe to screen by volatility.")
    parser.add_argument("--date", default=datetime.datetime.now().strftime("%Y-%m-%d"))
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    cfg = ExecutionConfig.from_env()
    candidates = [t.strip().upper() for t in args.tickers.split(",") if t.strip()]

    def history_fn(symbol: str) -> list[float]:
        import yfinance as yf
        return yf.Ticker(normalize_symbol(symbol)).history(period="1mo")["Close"].tolist()

    ledger_path = os.path.join(
        os.path.expanduser("~"), ".tradingagents", "execution", "ledger.jsonl"
    )
    run_cycle(
        args.date,
        cfg=cfg,
        broker=_default_broker(cfg),
        rating_fn=_default_rating,
        screen_fn=lambda: rank_by_volatility(candidates, history_fn, cfg.top_k),
        price_fn=_default_price,
        ledger=Ledger(ledger_path),
    )


if __name__ == "__main__":
    main()
