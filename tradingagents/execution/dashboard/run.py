from __future__ import annotations

import logging
from collections.abc import Iterator

from tradingagents.agents.utils.rating import parse_rating

from .. import position_policy
from ..config import ExecutionConfig
from .events import STAGES, events_from_state

logger = logging.getLogger(__name__)


def run_events(
    ticker: str,
    date: str,
    *,
    cfg: ExecutionConfig,
    broker,
    chunk_source,
    price_fn,
    ledger=None,
    cycle_id: str | None = None,
) -> Iterator[dict]:
    """Stream pipeline-stage events, then decide + submit the paper order."""
    cycle_id = cycle_id or f"{date}#1"
    stage_names = [name for _, name in STAGES]
    yield {"type": "run_started", "ticker": ticker, "date": date, "stages": stage_names}

    seen: set = set()
    final_state: dict = {}
    for kind, payload in chunk_source(ticker, date):
        if kind == "chunk":
            yield from events_from_state(payload, seen)
        elif kind == "final":
            final_state = payload

    decision_text = final_state.get("final_trade_decision", "")
    rating = parse_rating(decision_text)
    yield {"type": "decision", "rating": rating, "text": decision_text}

    price = price_fn(ticker)
    account = broker.account()
    position = broker.get_position(ticker)
    intents = position_policy.decide(ticker, rating, position, account, price, cfg, cycle_id)
    if not intents:
        yield {"type": "no_order", "reason": f"target matches current position (rating={rating})"}
    for intent in intents:
        order_id = broker.submit_order(intent)
        if ledger is not None:
            ledger.record_order(intent, order_id)
        yield {"type": "order", "side": intent.side, "symbol": intent.symbol,
               "qty": intent.qty, "order_id": order_id, "client_order_id": intent.client_order_id}
    yield {"type": "done"}


def default_chunk_source(ticker: str, date: str):
    """Real chunk source: stream the live graph (reuses public TradingAgentsGraph pieces)."""
    from tradingagents.default_config import DEFAULT_CONFIG
    from tradingagents.graph.trading_graph import TradingAgentsGraph

    ta = TradingAgentsGraph(config=DEFAULT_CONFIG.copy())
    past = ta.memory_log.get_past_context(ticker)
    instrument = ta.resolve_instrument_context(ticker, "stock")
    state = ta.propagator.create_initial_state(
        ticker, date, asset_type="stock", past_context=past, instrument_context=instrument
    )
    args = ta.propagator.get_graph_args()
    final: dict = {}
    for chunk in ta.graph.stream(state, **args):
        final.update(chunk)
        yield ("chunk", chunk)
    yield ("final", final)
