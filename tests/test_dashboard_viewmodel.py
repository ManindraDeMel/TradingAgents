from tradingagents.execution.broker.base import OrderIntent
from tradingagents.execution.broker.fake import FakeBroker
from tradingagents.execution.dashboard.viewmodel import portfolio_view
from tradingagents.execution.ledger import Ledger


def test_portfolio_view_from_broker_and_ledger(tmp_path):
    broker = FakeBroker(equity=5000.0, cash=5000.0)
    broker.submit_order(OrderIntent("AAPL", "buy", 3, "AAPL:c:0"))
    led = Ledger(tmp_path / "l.jsonl")
    led.snapshot_equity(5000.0, "2026-06-26")
    led.record_order(OrderIntent("AAPL", "buy", 3, "AAPL:c:0"), "o1")
    view = portfolio_view(broker, led)
    assert view["equity"] == 5000.0 and view["is_paper"] is True
    assert view["positions"][0]["symbol"] == "AAPL" and view["positions"][0]["qty"] == 3
    assert view["recent_orders"][-1]["order_id"] == "o1"
    assert view["pnl"]["snapshots"] == 1


def test_portfolio_view_without_ledger():
    view = portfolio_view(FakeBroker())
    assert view["recent_orders"] == [] and view["positions"] == []
