import dataclasses

import pytest

from tradingagents.execution.broker.base import Account, OrderIntent, Position


def test_order_intent_is_frozen():
    intent = OrderIntent(symbol="AAPL", side="buy", qty=10, client_order_id="AAPL:c:0")
    assert intent.reduce_only is False
    with pytest.raises(dataclasses.FrozenInstanceError):
        intent.qty = 20  # type: ignore[misc]


def test_position_qty_is_signed():
    short = Position(symbol="AAPL", qty=-5, avg_entry_price=100.0, market_value=-500.0)
    assert short.qty == -5


def test_account_fields():
    acct = Account(equity=100_000.0, cash=50_000.0, buying_power=100_000.0)
    assert acct.equity == 100_000.0
