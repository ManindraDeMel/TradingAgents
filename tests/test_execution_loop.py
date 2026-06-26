from tradingagents.execution.broker.base import OrderIntent
from tradingagents.execution.broker.fake import FakeBroker
from tradingagents.execution.config import ExecutionConfig
from tradingagents.execution.ledger import Ledger
from tradingagents.execution.loop import run_cycle

CFG = ExecutionConfig(per_name_pct=0.05, max_concurrent_positions=10, daily_loss_limit_pct=0.03)


def _cycle(broker, *, screen, rating, baseline=None, ledger=None, calendar=None):
    return run_cycle(
        "2026-06-27",
        cfg=CFG,
        broker=broker,
        rating_fn=lambda ticker, date: rating.get(ticker, "Hold"),
        screen_fn=lambda: screen,
        price_fn=lambda symbol: 100.0,
        baseline_equity=baseline,
        ledger=ledger,
        calendar_fn=calendar,
    )


def test_market_closed_does_nothing():
    broker = FakeBroker()
    summary = _cycle(broker, screen=["AAPL"], rating={"AAPL": "Buy"}, calendar=lambda d: False)
    assert summary["status"] == "market_closed"
    assert broker.submitted == []


def test_buy_on_screened_name_opens_position():
    broker = FakeBroker(equity=100_000.0, cash=100_000.0)
    summary = _cycle(broker, screen=["AAPL"], rating={"AAPL": "Buy"})
    assert summary["orders"] == 1
    assert broker.get_position("AAPL").qty == 50


def test_held_name_is_re_evaluated_even_if_not_screened():
    broker = FakeBroker(equity=100_000.0, cash=100_000.0)
    broker.submit_order(OrderIntent("AAPL", "buy", 50, "seed:0"))  # pre-existing long
    summary = _cycle(broker, screen=[], rating={"AAPL": "Sell"})  # flip to short
    assert broker.get_position("AAPL").qty == -50  # reversed in one cycle
    assert summary["orders"] == 2  # close + open


def test_kill_switch_halts_new_entries():
    broker = FakeBroker(equity=100_000.0, cash=100_000.0)
    # baseline far above current equity -> drawdown beyond the 3% limit
    summary = _cycle(broker, screen=["AAPL"], rating={"AAPL": "Buy"}, baseline=200_000.0)
    assert summary["kill_switch"] is True
    assert summary["orders"] == 0
    assert broker.submitted == []


def test_ledger_records_equity_and_orders(tmp_path):
    broker = FakeBroker(equity=100_000.0, cash=100_000.0)
    led = Ledger(tmp_path / "ledger.jsonl")
    _cycle(broker, screen=["AAPL"], rating={"AAPL": "Buy"}, ledger=led)
    types = [r["type"] for r in led.read()]
    assert "equity" in types and "order" in types and "cycle" in types
