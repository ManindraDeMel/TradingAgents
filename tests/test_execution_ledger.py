from tradingagents.execution.broker.base import OrderIntent
from tradingagents.execution.ledger import Ledger


def test_records_and_reads_back(tmp_path):
    led = Ledger(tmp_path / "sub" / "ledger.jsonl")
    led.snapshot_equity(100_000.0, "2026-06-27")
    led.record_order(OrderIntent("AAPL", "buy", 50, "AAPL:c:0"), "order-1")
    led.write_cycle_summary("2026-06-27", {"orders": 1})
    rows = led.read()
    assert [r["type"] for r in rows] == ["equity", "order", "cycle"]
    assert rows[1]["symbol"] == "AAPL" and rows[1]["order_id"] == "order-1"


def test_pnl_summary_from_equity_snapshots(tmp_path):
    led = Ledger(tmp_path / "ledger.jsonl")
    led.snapshot_equity(100_000.0, "2026-06-27")
    led.snapshot_equity(105_000.0, "2026-06-28")
    pnl = led.pnl_summary()
    assert pnl["snapshots"] == 2
    assert pnl["pnl"] == 5_000.0
    assert pnl["return_pct"] == 0.05


def test_pnl_summary_empty(tmp_path):
    pnl = Ledger(tmp_path / "ledger.jsonl").pnl_summary()
    assert pnl["snapshots"] == 0 and pnl["pnl"] == 0.0
