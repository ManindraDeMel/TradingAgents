# Alpaca Execution Loop — Slice 2 (Alpaca Adapter + trade-once) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add the real Alpaca paper-trading adapter and a one-shot `trade-once <ticker>` command that runs one agent analysis and places the matching paper order.

**Architecture:** `AlpacaBroker` implements the slice-1 `Broker` protocol over `alpaca-py`'s `TradingClient`. It takes an injectable client and imports `alpaca-py` lazily, so the package imports on a bare install and unit tests inject a fake client. `trade_once.run_trade_once()` is a dependency-injected orchestrator (broker, rating source, and price source are all injectable) wrapping `graph.propagate → position_policy.decide → broker.submit_order`; a thin `main()` exposes it as `python -m tradingagents.execution.trade_once`.

**Tech Stack:** Python 3.10+, `alpaca-py` (new optional `[alpaca]` extra), the slice-1 `execution` package, `yfinance` (already a dependency) for the latest price, pytest.

## Global Constraints

- Python floor 3.10 — `X | None`, `list[...]`, `from __future__ import annotations`. From `pyproject.toml`.
- Tests must require no real API keys, network, or LLM calls. Orchestration tests use `FakeBroker` + stub rating/price callables. The `AlpacaBroker` adapter test uses `pytest.importorskip("alpaca.trading.requests")` and a `MagicMock` client — it never hits Alpaca.
- `alpaca-py` is an **optional** dependency (`pip install ".[alpaca]"`), mirroring the existing `[bedrock]` extra. `AlpacaBroker` must import it lazily so `import tradingagents.execution.broker.alpaca` works without it installed (only constructing a real client or submitting an order needs it).
- Paper-only by default: constructing a live `AlpacaBroker` requires `allow_live=True`, never reachable by accident.
- Lint clean under `ruff check .` (strict select, `E501` ignored).
- Commit messages: conventional-commit prefix, no AI co-author trailer.
- alpaca-py API (verified via Context7 `/alpacahq/alpaca-py`): `TradingClient(api_key, secret_key, paper=True)`; `get_account()` → object with string `.equity`/`.cash`/`.buying_power`; `get_all_positions()` → list of objects with string `.qty`/`.avg_entry_price`/`.market_value` and `.side` (`PositionSide.LONG`/`.SHORT`); `get_open_position(symbol)` raises when none; `submit_order(order_data=MarketOrderRequest(symbol, qty, side=OrderSide.BUY|SELL, time_in_force=TimeInForce.DAY, client_order_id=...))` → order with `.id`; `close_position(symbol)`; `cancel_orders()`.

---

### Task 1: AlpacaBroker adapter (+ optional extra, env, CI)

**Files:**
- Modify: `pyproject.toml` (add `[alpaca]` optional-dependency extra)
- Modify: `.env.example` (add `ALPACA_API_KEY` / `ALPACA_SECRET_KEY`)
- Modify: `.github/workflows/ci.yml` (install `.[dev,alpaca]` so adapter tests run in CI)
- Create: `tradingagents/execution/broker/alpaca.py`
- Test: `tests/test_execution_alpaca_broker.py`

**Interfaces:**
- Consumes: `Account`, `OrderIntent`, `Position`, `Broker` from `tradingagents/execution/broker/base.py`.
- Produces: `AlpacaBroker(client=None, *, api_key=None, secret_key=None, paper=True, allow_live=False)` — a `Broker` implementation. `is_paper` is set from `paper`. Constructing with `paper=False` and `allow_live=False` raises `ValueError`. When `client is None`, lazily builds `alpaca.trading.client.TradingClient`. Maps Alpaca's string fields to the float/int dataclass fields; signs `Position.qty` negative for `PositionSide.SHORT`. `get_position`/`close_position` return `None` on any client error.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_execution_alpaca_broker.py
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_execution_alpaca_broker.py -q`
Expected: collection error / skip — `No module named 'tradingagents.execution.broker.alpaca'` once `alpaca-py` is installed (next step). Before installing the extra, the module-level `importorskip` skips the file.

- [ ] **Step 3: Add the optional extra and install it**

In `pyproject.toml`, add after the `bedrock` extra block:

```toml
# Alpaca paper-trading execution adapter (alpaca-py). Optional so the core
# install stays lean: pip install "tradingagents[alpaca]".
alpaca = [
    "alpaca-py>=0.30",
]
```

Then install locally:

Run: `.venv/bin/python -m pip install -e ".[alpaca]"`
Expected: installs `alpaca-py` and its deps.

- [ ] **Step 4: Add credentials to `.env.example`**

Insert after the `OPENAI_COMPATIBLE_API_KEY` block (before the Bedrock block):

```bash
# Alpaca paper-trading execution layer (install with: pip install ".[alpaca]").
# Generate PAPER keys at https://app.alpaca.markets/ (Paper Trading -> API Keys).
# The execution layer defaults to the paper endpoint; live trading is gated.
#ALPACA_API_KEY=
#ALPACA_SECRET_KEY=
```

- [ ] **Step 5: Write the implementation**

```python
# tradingagents/execution/broker/alpaca.py
from __future__ import annotations

from .base import Account, OrderIntent, Position


class AlpacaBroker:
    """Broker implementation backed by alpaca-py's TradingClient.

    alpaca-py is imported lazily so this module imports without the optional
    ``[alpaca]`` extra installed; only constructing a real client or submitting
    an order pulls it in. Tests inject a fake ``client``.
    """

    def __init__(
        self,
        client=None,
        *,
        api_key: str | None = None,
        secret_key: str | None = None,
        paper: bool = True,
        allow_live: bool = False,
    ) -> None:
        if not paper and not allow_live:
            raise ValueError(
                "Refusing to construct a live AlpacaBroker without allow_live=True. "
                "Paper trading is the default; pass allow_live=True to trade real money."
            )
        self.is_paper = paper
        if client is None:
            from alpaca.trading.client import TradingClient

            client = TradingClient(api_key, secret_key, paper=paper)
        self._client = client

    def account(self) -> Account:
        a = self._client.get_account()
        return Account(
            equity=float(a.equity),
            cash=float(a.cash),
            buying_power=float(a.buying_power),
        )

    def positions(self) -> list[Position]:
        return [self._to_position(p) for p in self._client.get_all_positions()]

    def get_position(self, symbol: str) -> Position | None:
        try:
            return self._to_position(self._client.get_open_position(symbol))
        except Exception:
            # alpaca-py raises when there is no open position for the symbol.
            return None

    def submit_order(self, intent: OrderIntent) -> str:
        from alpaca.trading.enums import OrderSide, TimeInForce
        from alpaca.trading.requests import MarketOrderRequest

        req = MarketOrderRequest(
            symbol=intent.symbol,
            qty=intent.qty,
            side=OrderSide.BUY if intent.side == "buy" else OrderSide.SELL,
            time_in_force=TimeInForce.DAY,
            client_order_id=intent.client_order_id,
        )
        order = self._client.submit_order(order_data=req)
        return str(order.id)

    def cancel_all(self) -> None:
        self._client.cancel_orders()

    def close_position(self, symbol: str) -> str | None:
        try:
            order = self._client.close_position(symbol)
        except Exception:
            return None
        return str(getattr(order, "id", "")) or None

    @staticmethod
    def _to_position(p) -> Position:
        from alpaca.trading.enums import PositionSide

        qty = abs(int(float(p.qty)))
        signed = -qty if p.side == PositionSide.SHORT else qty
        return Position(
            symbol=p.symbol,
            qty=signed,
            avg_entry_price=float(p.avg_entry_price),
            market_value=float(p.market_value),
        )
```

- [ ] **Step 6: Update CI to install the extra so the adapter tests run**

In `.github/workflows/ci.yml`, in the `test` job's install step, change:

```yaml
          pip install -e ".[dev]"
```

to:

```yaml
          pip install -e ".[dev,alpaca]"
```

(Leave the `smoke-install` job as a bare `pip install .` — it verifies the package imports with no extras, which the lazy `alpaca-py` import preserves.)

- [ ] **Step 7: Run the adapter tests + lint**

Run: `.venv/bin/python -m pytest tests/test_execution_alpaca_broker.py -q && .venv/bin/python -m ruff check tradingagents/execution/broker/alpaca.py tests/test_execution_alpaca_broker.py`
Expected: 8 passed; ruff clean.

- [ ] **Step 8: Commit**

```bash
git add pyproject.toml .env.example .github/workflows/ci.yml tradingagents/execution/broker/alpaca.py tests/test_execution_alpaca_broker.py
git commit -m "feat(execution): add AlpacaBroker paper-trading adapter behind the [alpaca] extra"
```

---

### Task 2: trade-once orchestrator + CLI

**Files:**
- Create: `tradingagents/execution/trade_once.py`
- Test: `tests/test_execution_trade_once.py`

**Interfaces:**
- Consumes: `Broker`, `OrderIntent` from `broker/base.py`; `FakeBroker` from `broker/fake.py` (tests); `ExecutionConfig` from `config.py`; `decide` from `position_policy.py`.
- Produces:
  `run_trade_once(ticker: str, date: str, *, cfg: ExecutionConfig | None = None, broker: Broker | None = None, rating_fn=None, price_fn=None, cycle_id: str | None = None) -> list[OrderIntent]`
  — runs `rating_fn(ticker, date)` (default: a real `TradingAgentsGraph.propagate`), `price_fn(ticker)` (default: latest yfinance close), reads account + current position from `broker` (default: a paper `AlpacaBroker` from env keys), computes intents via `position_policy.decide`, submits each, and returns the intents. `cycle_id` defaults to `f"{date}#1"`. Also `main(argv=None) -> None` (argparse: positional `ticker`, `--date` defaulting to today) for `python -m tradingagents.execution.trade_once`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_execution_trade_once.py
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_execution_trade_once.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'tradingagents.execution.trade_once'`.

- [ ] **Step 3: Write the implementation**

```python
# tradingagents/execution/trade_once.py
from __future__ import annotations

import argparse
import logging

from . import position_policy
from .broker.base import Broker, OrderIntent
from .config import ExecutionConfig

logger = logging.getLogger(__name__)


def _default_price(symbol: str) -> float:
    import yfinance as yf

    from tradingagents.dataflows.symbol_utils import normalize_symbol

    hist = yf.Ticker(normalize_symbol(symbol)).history(period="1d")
    return float(hist["Close"].iloc[-1])


def _default_broker(cfg: ExecutionConfig) -> Broker:
    import os

    from .broker.alpaca import AlpacaBroker

    return AlpacaBroker(
        api_key=os.environ.get("ALPACA_API_KEY"),
        secret_key=os.environ.get("ALPACA_SECRET_KEY"),
        paper=not cfg.allow_live,
        allow_live=cfg.allow_live,
    )


def _default_rating(ticker: str, date: str) -> str:
    from tradingagents.default_config import DEFAULT_CONFIG
    from tradingagents.graph.trading_graph import TradingAgentsGraph

    graph = TradingAgentsGraph(config=DEFAULT_CONFIG.copy())
    _, rating = graph.propagate(ticker, date)
    return rating


def run_trade_once(
    ticker: str,
    date: str,
    *,
    cfg: ExecutionConfig | None = None,
    broker: Broker | None = None,
    rating_fn=None,
    price_fn=None,
    cycle_id: str | None = None,
) -> list[OrderIntent]:
    """Run one agent analysis for ``ticker`` and place the matching paper order(s)."""
    cfg = cfg or ExecutionConfig.from_env()
    broker = broker or _default_broker(cfg)
    rating_fn = rating_fn or _default_rating
    price_fn = price_fn or _default_price
    cycle_id = cycle_id or f"{date}#1"

    rating = rating_fn(ticker, date)
    price = price_fn(ticker)
    account = broker.account()
    position = broker.get_position(ticker)

    intents = position_policy.decide(ticker, rating, position, account, price, cfg, cycle_id)
    for intent in intents:
        order_id = broker.submit_order(intent)
        logger.info(
            "Submitted %s %s x%d (%s) -> %s",
            intent.side, intent.symbol, intent.qty, intent.client_order_id, order_id,
        )
    if not intents:
        logger.info("No order for %s (rating=%s): target matches current position.", ticker, rating)
    return intents


def main(argv: list[str] | None = None) -> None:
    import datetime

    parser = argparse.ArgumentParser(
        prog="trade-once",
        description="Run one agent analysis and place the matching Alpaca paper order.",
    )
    parser.add_argument("ticker")
    parser.add_argument("--date", default=datetime.datetime.now().strftime("%Y-%m-%d"))
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    run_trade_once(args.ticker, args.date)


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run the trade-once tests**

Run: `.venv/bin/python -m pytest tests/test_execution_trade_once.py -q`
Expected: PASS (4 passed).

- [ ] **Step 5: Run the full suite + lint**

Run: `.venv/bin/python -m pytest -q && .venv/bin/python -m ruff check tradingagents/execution tests/test_execution_*.py`
Expected: all tests pass (the 23 slice-1 tests + 8 adapter + 4 trade-once); ruff clean.

- [ ] **Step 6: Commit**

```bash
git add tradingagents/execution/trade_once.py tests/test_execution_trade_once.py
git commit -m "feat(execution): add trade-once orchestrator wiring a run to a paper order"
```

---

## Self-Review

**1. Spec coverage (slice 2):**
- Spec §4 `broker/alpaca.py` `AlpacaBroker`, paper-default, live-gated → Task 1. ✓
- Spec §9 `alpaca-py` as an optional `[alpaca]` extra + `ALPACA_*` in `.env.example` → Task 1 Steps 3–4. ✓
- Spec §10 "AlpacaBroker unit-tested against a mocked alpaca-py client; no live calls" → Task 1 test (MagicMock client, `importorskip`). ✓
- Spec §11 slice 2 = "AlpacaBroker (paper) + a one-shot trade-once <ticker> command" → Tasks 1 + 2. ✓
- Spec §5/§6 reuse: `trade_once` calls `position_policy.decide` (slice 1) and submits via the `Broker` seam → Task 2. ✓
- Carried to later slices (correctly out of scope): screener, risk caps/kill-switch, the daily loop, ledger (slices 3–4).

**2. Placeholder scan:** No TBD/TODO/"handle edge cases". Every code step is complete. The broad `except Exception` in `get_position`/`close_position` is intentional (alpaca-py raises a typed error when a position is absent; we fail soft to `None`) and documented in a comment.

**3. Type consistency:** `AlpacaBroker` maps to the exact slice-1 `Account`/`Position` field names and the `Broker` method signatures (`account`/`positions`/`get_position`/`submit_order`/`cancel_all`/`close_position`, `is_paper`). `run_trade_once` calls `decide(symbol, rating, position, account, price, cfg, cycle_id)` matching slice-1 Task 4's signature exactly, and consumes the `OrderIntent` it returns. `FakeBroker(equity=, cash=)` matches slice-1 Task 3.
