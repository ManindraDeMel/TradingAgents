import math
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from tradingagents.execution.screener import rank_by_volatility, realized_volatility


def test_realized_volatility_constant_returns_is_zero():
    assert realized_volatility([100.0, 110.0, 121.0]) == 0.0  # +10%, +10%


def test_realized_volatility_symmetric_swing():
    # returns +0.1 then -0.1 -> mean 0, population stdev 0.1
    assert math.isclose(realized_volatility([100.0, 110.0, 99.0]), 0.1, rel_tol=1e-9)


def test_realized_volatility_too_short_is_zero():
    assert realized_volatility([100.0]) == 0.0
    assert realized_volatility([]) == 0.0


def test_rank_by_volatility_takes_most_volatile_top_k():
    history = {
        "CALM": [100.0, 100.5, 101.0],     # tiny vol
        "WILD": [100.0, 120.0, 80.0],      # huge vol
        "MILD": [100.0, 103.0, 100.0],     # medium vol
    }
    ranked = rank_by_volatility(["CALM", "WILD", "MILD"], lambda s: history[s], top_k=2)
    assert ranked == ["WILD", "MILD"]


def test_fetch_alpaca_candidates_combines_and_dedupes():
    pytest.importorskip("alpaca.data.requests")
    from tradingagents.execution.screener import fetch_alpaca_candidates

    client = MagicMock()
    client.get_most_actives.return_value = SimpleNamespace(
        most_actives=[SimpleNamespace(symbol="AAPL"), SimpleNamespace(symbol="MSFT")]
    )
    client.get_market_movers.return_value = SimpleNamespace(
        gainers=[SimpleNamespace(symbol="NVDA")],
        losers=[SimpleNamespace(symbol="AAPL")],  # duplicate of an active
    )
    assert fetch_alpaca_candidates(client, top=10) == ["AAPL", "MSFT", "NVDA"]
