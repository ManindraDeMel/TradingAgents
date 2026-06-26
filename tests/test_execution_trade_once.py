from tradingagents.execution.broker.fake import FakeBroker
from tradingagents.execution.config import ExecutionConfig
from tradingagents.execution.trade_once import run_trade_once

CFG = ExecutionConfig(per_name_pct=0.05)


def _run(rating, broker):
    return run_trade_once(
        "AAPL",
        "2026-06-27",
        cfg=CFG,
        broker=broker,
        rating_fn=lambda ticker, date: rating,
        price_fn=lambda symbol: 100.0,
    )


def test_buy_rating_submits_one_order_and_opens_position():
    broker = FakeBroker(equity=100_000.0, cash=100_000.0)
    intents = _run("Buy", broker)
    assert len(intents) == 1
    assert broker.submitted[0].side == "buy"
    assert broker.get_position("AAPL").qty == 50  # 5% of 100k / $100


def test_hold_rating_submits_nothing():
    broker = FakeBroker()
    intents = _run("Hold", broker)
    assert intents == []
    assert broker.submitted == []


def test_client_order_id_uses_cycle_id_from_date():
    broker = FakeBroker(equity=100_000.0, cash=100_000.0)
    _run("Buy", broker)
    assert broker.submitted[0].client_order_id == "AAPL:2026-06-27#1:0"


def test_sell_from_flat_opens_short():
    broker = FakeBroker(equity=100_000.0, cash=100_000.0)
    intents = _run("Sell", broker)
    assert intents[0].side == "sell"
    assert broker.get_position("AAPL").qty == -50
