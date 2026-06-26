from tradingagents.execution.broker.base import Broker, OrderIntent
from tradingagents.execution.broker.fake import FakeBroker


def test_fakebroker_satisfies_protocol():
    assert isinstance(FakeBroker(), Broker)
    assert FakeBroker().is_paper is True


def test_buy_opens_long_position():
    broker = FakeBroker()
    order_id = broker.submit_order(
        OrderIntent(symbol="AAPL", side="buy", qty=50, client_order_id="AAPL:c:0")
    )
    assert order_id == "fake-1"
    assert broker.submitted[0].qty == 50
    assert broker.get_position("AAPL").qty == 50


def test_reversal_pair_ends_net_short():
    broker = FakeBroker()
    broker.submit_order(OrderIntent("AAPL", "buy", 50, "AAPL:c1:0"))
    # reverse: close the long, then open a short
    broker.submit_order(OrderIntent("AAPL", "sell", 50, "AAPL:c2:0", reduce_only=True))
    broker.submit_order(OrderIntent("AAPL", "sell", 50, "AAPL:c2:1"))
    assert broker.get_position("AAPL").qty == -50
    assert len(broker.submitted) == 3


def test_position_netting_to_zero_is_removed():
    broker = FakeBroker()
    broker.submit_order(OrderIntent("AAPL", "buy", 50, "AAPL:c1:0"))
    broker.submit_order(OrderIntent("AAPL", "sell", 50, "AAPL:c2:0", reduce_only=True))
    assert broker.get_position("AAPL") is None
    assert broker.positions() == []
