from tradingagents.execution.broker.fake import FakeBroker
from tradingagents.execution.config import ExecutionConfig
from tradingagents.execution.dashboard.events import events_from_state
from tradingagents.execution.dashboard.run import run_events


def test_events_from_state_emits_each_key_once():
    seen = set()
    e1 = events_from_state({"market_report": "m"}, seen)
    assert e1 == [{"type": "stage_complete", "stage": "Market Analyst", "text": "m"}]
    # same key again -> nothing; new key -> one event
    assert events_from_state({"market_report": "m"}, seen) == []
    e2 = events_from_state({"market_report": "m", "news_report": "n"}, seen)
    assert e2 == [{"type": "stage_complete", "stage": "News Analyst", "text": "n"}]


def test_run_events_buy_streams_then_orders():
    def chunk_source(ticker, date):
        yield ("chunk", {"market_report": "ok"})
        yield ("chunk", {"market_report": "ok", "final_trade_decision": "Rating: Buy\nlong it"})
        yield ("final", {"final_trade_decision": "Rating: Buy\nlong it"})

    broker = FakeBroker(equity=100_000.0, cash=100_000.0)
    events = list(run_events(
        "AAPL", "2026-06-27",
        cfg=ExecutionConfig(per_name_pct=0.05),
        broker=broker, chunk_source=chunk_source, price_fn=lambda s: 100.0,
    ))
    types = [e["type"] for e in events]
    assert types[0] == "run_started" and types[-1] == "done"
    assert "stage_complete" in types
    decision = next(e for e in events if e["type"] == "decision")
    assert decision["rating"] == "Buy"
    order = next(e for e in events if e["type"] == "order")
    assert order["side"] == "buy" and order["qty"] == 50
    assert broker.get_position("AAPL").qty == 50


def test_run_events_hold_emits_no_order():
    def chunk_source(t, d):
        yield ("final", {"final_trade_decision": "Rating: Hold"})
    events = list(run_events("AAPL", "2026-06-27", cfg=ExecutionConfig(),
                             broker=FakeBroker(), chunk_source=chunk_source, price_fn=lambda s: 100.0))
    assert any(e["type"] == "no_order" for e in events)
    assert not any(e["type"] == "order" for e in events)
