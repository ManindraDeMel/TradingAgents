from tradingagents.execution.broker.base import Account, Position
from tradingagents.execution.config import ExecutionConfig
from tradingagents.execution.position_policy import decide

CFG = ExecutionConfig(per_name_pct=0.05)            # 5% of equity
ACCT = Account(equity=100_000.0, cash=100_000.0, buying_power=200_000.0)
PRICE = 100.0                                        # full = 5000/100 = 50 shares; half = 25
CYCLE = "2026-06-27#1"


def _only(intents):
    assert len(intents) == 1
    return intents[0]


def test_flat_buy_opens_full_long():
    intent = _only(decide("AAPL", "Buy", None, ACCT, PRICE, CFG, CYCLE))
    assert (intent.side, intent.qty, intent.reduce_only) == ("buy", 50, False)
    assert intent.client_order_id == "AAPL:2026-06-27#1:0"


def test_flat_overweight_opens_half_long():
    intent = _only(decide("AAPL", "Overweight", None, ACCT, PRICE, CFG, CYCLE))
    assert (intent.side, intent.qty) == ("buy", 25)


def test_flat_sell_opens_full_short():
    intent = _only(decide("AAPL", "Sell", None, ACCT, PRICE, CFG, CYCLE))
    assert (intent.side, intent.qty) == ("sell", 50)


def test_flat_underweight_opens_half_short():
    intent = _only(decide("AAPL", "Underweight", None, ACCT, PRICE, CFG, CYCLE))
    assert (intent.side, intent.qty) == ("sell", 25)


def test_hold_does_nothing():
    assert decide("AAPL", "Hold", None, ACCT, PRICE, CFG, CYCLE) == []


def test_unknown_rating_treated_as_hold():
    assert decide("AAPL", "NO_DATA", None, ACCT, PRICE, CFG, CYCLE) == []


def test_long_to_sell_reverses_in_one_cycle():
    pos = Position("AAPL", qty=50, avg_entry_price=100.0, market_value=5000.0)
    intents = decide("AAPL", "Sell", pos, ACCT, PRICE, CFG, CYCLE)
    assert len(intents) == 2
    close, open_ = intents
    assert (close.side, close.qty, close.reduce_only) == ("sell", 50, True)
    assert close.client_order_id == "AAPL:2026-06-27#1:0"
    assert (open_.side, open_.qty, open_.reduce_only) == ("sell", 50, False)
    assert open_.client_order_id == "AAPL:2026-06-27#1:1"


def test_short_to_buy_reverses_in_one_cycle():
    pos = Position("AAPL", qty=-50, avg_entry_price=100.0, market_value=-5000.0)
    intents = decide("AAPL", "Buy", pos, ACCT, PRICE, CFG, CYCLE)
    assert len(intents) == 2
    close, open_ = intents
    assert (close.side, close.qty, close.reduce_only) == ("buy", 50, True)
    assert (open_.side, open_.qty, open_.reduce_only) == ("buy", 50, False)


def test_trim_long_to_half_same_side_reduce_only():
    pos = Position("AAPL", qty=50, avg_entry_price=100.0, market_value=5000.0)
    intent = _only(decide("AAPL", "Overweight", pos, ACCT, PRICE, CFG, CYCLE))
    assert (intent.side, intent.qty, intent.reduce_only) == ("sell", 25, True)


def test_add_to_long_same_side_not_reduce_only():
    pos = Position("AAPL", qty=25, avg_entry_price=100.0, market_value=2500.0)
    intent = _only(decide("AAPL", "Buy", pos, ACCT, PRICE, CFG, CYCLE))
    assert (intent.side, intent.qty, intent.reduce_only) == ("buy", 25, False)


def test_target_equals_current_does_nothing():
    pos = Position("AAPL", qty=50, avg_entry_price=100.0, market_value=5000.0)
    assert decide("AAPL", "Buy", pos, ACCT, PRICE, CFG, CYCLE) == []
