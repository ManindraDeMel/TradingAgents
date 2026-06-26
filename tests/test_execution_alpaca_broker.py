from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

# The adapter test exercises real alpaca-py request/enum classes; skip cleanly
# when the optional extra isn't installed (keeps a dev-only install green).
pytest.importorskip("alpaca.trading.requests")

from alpaca.trading.enums import OrderSide, PositionSide, TimeInForce  # noqa: E402

from tradingagents.execution.broker.alpaca import AlpacaBroker  # noqa: E402
from tradingagents.execution.broker.base import Broker, OrderIntent  # noqa: E402


def _broker(client):
    return AlpacaBroker(client=client, paper=True)


def test_satisfies_protocol_and_is_paper():
    broker = _broker(MagicMock())
    assert isinstance(broker, Broker)
    assert broker.is_paper is True


def test_live_without_allow_live_raises():
    with pytest.raises(ValueError, match="live"):
        AlpacaBroker(client=MagicMock(), paper=False)


def test_live_with_allow_live_is_allowed():
    broker = AlpacaBroker(client=MagicMock(), paper=False, allow_live=True)
    assert broker.is_paper is False


def test_account_maps_string_fields_to_floats():
    client = MagicMock()
    client.get_account.return_value = SimpleNamespace(
        equity="100000", cash="50000", buying_power="200000"
    )
    acct = _broker(client).account()
    assert (acct.equity, acct.cash, acct.buying_power) == (100000.0, 50000.0, 200000.0)


def test_positions_sign_from_side():
    client = MagicMock()
    client.get_all_positions.return_value = [
        SimpleNamespace(symbol="AAPL", qty="10", side=PositionSide.LONG,
                        avg_entry_price="100", market_value="1000"),
        SimpleNamespace(symbol="TSLA", qty="-5", side=PositionSide.SHORT,
                        avg_entry_price="200", market_value="-1000"),
    ]
    by_symbol = {p.symbol: p for p in _broker(client).positions()}
    assert by_symbol["AAPL"].qty == 10
    assert by_symbol["TSLA"].qty == -5


def test_get_position_returns_none_when_absent():
    client = MagicMock()
    client.get_open_position.side_effect = Exception("position does not exist")
    assert _broker(client).get_position("AAPL") is None


def test_submit_order_builds_market_order_request():
    client = MagicMock()
    client.submit_order.return_value = SimpleNamespace(id="order-123")
    order_id = _broker(client).submit_order(
        OrderIntent(symbol="AAPL", side="buy", qty=10, client_order_id="AAPL:c:0")
    )
    assert order_id == "order-123"
    req = client.submit_order.call_args.kwargs["order_data"]
    assert req.symbol == "AAPL"
    assert int(req.qty) == 10
    assert req.side == OrderSide.BUY
    assert req.time_in_force == TimeInForce.DAY
    assert req.client_order_id == "AAPL:c:0"


def test_submit_sell_maps_to_sell_side():
    client = MagicMock()
    client.submit_order.return_value = SimpleNamespace(id="o1")
    _broker(client).submit_order(OrderIntent("AAPL", "sell", 5, "AAPL:c:1"))
    assert client.submit_order.call_args.kwargs["order_data"].side == OrderSide.SELL
