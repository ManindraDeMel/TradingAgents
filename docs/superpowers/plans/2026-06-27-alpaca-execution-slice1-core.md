# Alpaca Execution Loop — Slice 1 (Testable Core) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the network-free core of the Alpaca execution layer — config, broker interface, an in-memory fake broker, and the pure rating→order decision function — fully unit-tested.

**Architecture:** A new `tradingagents/execution/` package. A `Broker` Protocol abstracts order placement so an in-memory `FakeBroker` (this slice) and `AlpacaBroker` (slice 2) are interchangeable. `position_policy.decide()` is a pure function mapping a 5-tier rating + current position to a list of `OrderIntent`s (long/short, conviction-weighted, reverse-in-one-cycle). No I/O, no clock, no network in this slice.

**Tech Stack:** Python 3.10+, dataclasses, `typing.Protocol`, pytest. No new third-party dependencies in this slice (`alpaca-py` arrives in slice 2).

## Global Constraints

- Python floor: target ≥ 3.10 — use `X | None` unions and `list[...]` builtins (no `typing.Optional`/`List`). Copied from `pyproject.toml` `requires-python = ">=3.10"`.
- Tests must require no real API keys, network, or LLM calls. `tests/conftest.py` already injects placeholder keys and resets dataflows config; new tests add nothing that needs live services.
- Lint clean under `ruff check .` with the repo's strict select (`E,W,F,I,B,UP,C4,SIM`); `E501` is ignored. Use `from __future__ import annotations` at the top of each module (repo convention).
- Ratings are the canonical 5-tier strings from `tradingagents/agents/utils/rating.py`: `Buy`, `Overweight`, `Hold`, `Underweight`, `Sell`. Any other/missing rating is treated as `Hold` (no action) — never fabricate a trade.
- Commit messages: conventional-commit prefix, no AI co-author trailer.
- New tests live flat under `tests/` (repo convention: `tests/test_*.py`, no nested package dirs).

---

### Task 1: ExecutionConfig

**Files:**
- Create: `tradingagents/execution/__init__.py` (empty)
- Create: `tradingagents/execution/config.py`
- Test: `tests/test_execution_config.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `ExecutionConfig` (frozen dataclass) with fields `per_name_pct: float = 0.05`, `max_concurrent_positions: int = 10`, `max_gross_exposure_pct: float = 1.0`, `daily_loss_limit_pct: float = 0.03`, `kill_switch_flatten: bool = False`, `top_k: int = 10`, `allow_live: bool = False`. Classmethod `ExecutionConfig.from_env() -> ExecutionConfig` reads `TRADINGAGENTS_EXEC_<FIELD_UPPERCASE>` env vars, coercing to each field's type and raising `ValueError` on invalid input.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_execution_config.py
import pytest

from tradingagents.execution.config import ExecutionConfig


def test_defaults():
    cfg = ExecutionConfig()
    assert cfg.per_name_pct == 0.05
    assert cfg.max_concurrent_positions == 10
    assert cfg.allow_live is False


def test_from_env_overrides_typed(monkeypatch):
    monkeypatch.setenv("TRADINGAGENTS_EXEC_PER_NAME_PCT", "0.1")
    monkeypatch.setenv("TRADINGAGENTS_EXEC_MAX_CONCURRENT_POSITIONS", "4")
    monkeypatch.setenv("TRADINGAGENTS_EXEC_ALLOW_LIVE", "true")
    cfg = ExecutionConfig.from_env()
    assert cfg.per_name_pct == 0.1
    assert cfg.max_concurrent_positions == 4
    assert cfg.allow_live is True


def test_from_env_unset_uses_defaults():
    cfg = ExecutionConfig.from_env()
    assert cfg.top_k == 10


def test_from_env_invalid_bool_raises(monkeypatch):
    monkeypatch.setenv("TRADINGAGENTS_EXEC_ALLOW_LIVE", "treu")
    with pytest.raises(ValueError, match="ALLOW_LIVE"):
        ExecutionConfig.from_env()


def test_from_env_invalid_int_raises(monkeypatch):
    monkeypatch.setenv("TRADINGAGENTS_EXEC_TOP_K", "ten")
    with pytest.raises(ValueError, match="TOP_K"):
        ExecutionConfig.from_env()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_execution_config.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'tradingagents.execution'`.

- [ ] **Step 3: Create the empty package marker**

Create `tradingagents/execution/__init__.py` with no content (empty file).

- [ ] **Step 4: Write minimal implementation**

```python
# tradingagents/execution/config.py
from __future__ import annotations

import os
from dataclasses import dataclass, fields

_ENV_PREFIX = "TRADINGAGENTS_EXEC_"
_BOOL_TRUE = ("true", "1", "yes", "on")
_BOOL_FALSE = ("false", "0", "no", "off")


def _coerce(value: str, reference: object) -> object:
    """Coerce an env string to the type of the field's default value."""
    if isinstance(reference, bool):
        normalized = value.strip().lower()
        if normalized in _BOOL_TRUE:
            return True
        if normalized in _BOOL_FALSE:
            return False
        raise ValueError(f"expected a boolean, got {value!r}")
    if isinstance(reference, int) and not isinstance(reference, bool):
        return int(value)
    if isinstance(reference, float):
        return float(value)
    return value


@dataclass(frozen=True)
class ExecutionConfig:
    per_name_pct: float = 0.05
    max_concurrent_positions: int = 10
    max_gross_exposure_pct: float = 1.0
    daily_loss_limit_pct: float = 0.03
    kill_switch_flatten: bool = False
    top_k: int = 10
    allow_live: bool = False

    @classmethod
    def from_env(cls) -> "ExecutionConfig":
        overrides: dict[str, object] = {}
        for f in fields(cls):
            env_var = _ENV_PREFIX + f.name.upper()
            raw = os.environ.get(env_var)
            if raw is None or raw == "":
                continue
            try:
                overrides[f.name] = _coerce(raw, f.default)
            except ValueError as exc:
                raise ValueError(f"Invalid value for {env_var}: {exc}") from exc
        return cls(**overrides)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_execution_config.py -v`
Expected: PASS (5 passed).

- [ ] **Step 6: Commit**

```bash
git add tradingagents/execution/__init__.py tradingagents/execution/config.py tests/test_execution_config.py
git commit -m "feat(execution): add ExecutionConfig with TRADINGAGENTS_EXEC_* env overrides"
```

---

### Task 2: Broker protocol + dataclasses

**Files:**
- Create: `tradingagents/execution/broker/__init__.py` (empty)
- Create: `tradingagents/execution/broker/base.py`
- Test: `tests/test_execution_broker_base.py`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `OrderIntent(symbol: str, side: str, qty: int, client_order_id: str, reduce_only: bool = False)` — frozen dataclass. `side` is `"buy"` or `"sell"`; `qty` is whole shares > 0.
  - `Position(symbol: str, qty: int, avg_entry_price: float, market_value: float)` — frozen; `qty` signed (+long / -short).
  - `Account(equity: float, cash: float, buying_power: float)` — frozen.
  - `Broker` — `runtime_checkable` Protocol with attribute `is_paper: bool` and methods `account() -> Account`, `positions() -> list[Position]`, `get_position(symbol: str) -> Position | None`, `submit_order(intent: OrderIntent) -> str`, `cancel_all() -> None`, `close_position(symbol: str) -> str | None`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_execution_broker_base.py
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_execution_broker_base.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'tradingagents.execution.broker'`.

- [ ] **Step 3: Create the empty package marker**

Create `tradingagents/execution/broker/__init__.py` with no content (empty file).

- [ ] **Step 4: Write minimal implementation**

```python
# tradingagents/execution/broker/base.py
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class OrderIntent:
    symbol: str
    side: str               # "buy" | "sell"
    qty: int                # whole shares, > 0
    client_order_id: str    # idempotency key: f"{symbol}:{cycle_id}:{leg}"
    reduce_only: bool = False


@dataclass(frozen=True)
class Position:
    symbol: str
    qty: int                # signed: + long, - short
    avg_entry_price: float
    market_value: float


@dataclass(frozen=True)
class Account:
    equity: float
    cash: float
    buying_power: float


@runtime_checkable
class Broker(Protocol):
    is_paper: bool

    def account(self) -> Account: ...
    def positions(self) -> list[Position]: ...
    def get_position(self, symbol: str) -> Position | None: ...
    def submit_order(self, intent: OrderIntent) -> str: ...
    def cancel_all(self) -> None: ...
    def close_position(self, symbol: str) -> str | None: ...
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_execution_broker_base.py -v`
Expected: PASS (3 passed).

- [ ] **Step 6: Commit**

```bash
git add tradingagents/execution/broker/__init__.py tradingagents/execution/broker/base.py tests/test_execution_broker_base.py
git commit -m "feat(execution): add Broker protocol and OrderIntent/Position/Account dataclasses"
```

---

### Task 3: FakeBroker (in-memory)

**Files:**
- Create: `tradingagents/execution/broker/fake.py`
- Test: `tests/test_execution_fake_broker.py`

**Interfaces:**
- Consumes: `Account`, `OrderIntent`, `Position`, `Broker` from `tradingagents/execution/broker/base.py`.
- Produces: `FakeBroker(equity: float = 100_000.0, cash: float = 100_000.0)` — an in-memory `Broker` implementation. `is_paper = True`. Records every submitted intent in `.submitted: list[OrderIntent]` and applies an immediate fill that updates signed positions (a position that nets to 0 is removed). `submit_order` returns `f"fake-{n}"`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_execution_fake_broker.py
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_execution_fake_broker.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'tradingagents.execution.broker.fake'`.

- [ ] **Step 3: Write minimal implementation**

```python
# tradingagents/execution/broker/fake.py
from __future__ import annotations

from .base import Account, OrderIntent, Position


class FakeBroker:
    """In-memory Broker implementation for tests. Fills orders immediately."""

    is_paper = True

    def __init__(self, equity: float = 100_000.0, cash: float = 100_000.0) -> None:
        self._account = Account(equity=equity, cash=cash, buying_power=cash * 2)
        self._positions: dict[str, Position] = {}
        self.submitted: list[OrderIntent] = []
        self._order_seq = 0

    def account(self) -> Account:
        return self._account

    def positions(self) -> list[Position]:
        return list(self._positions.values())

    def get_position(self, symbol: str) -> Position | None:
        return self._positions.get(symbol)

    def submit_order(self, intent: OrderIntent) -> str:
        self.submitted.append(intent)
        self._apply_fill(intent)
        self._order_seq += 1
        return f"fake-{self._order_seq}"

    def cancel_all(self) -> None:
        return None

    def close_position(self, symbol: str) -> str | None:
        if self._positions.pop(symbol, None) is None:
            return None
        self._order_seq += 1
        return f"fake-close-{self._order_seq}"

    def _apply_fill(self, intent: OrderIntent) -> None:
        delta = intent.qty if intent.side == "buy" else -intent.qty
        existing = self._positions.get(intent.symbol)
        new_qty = (existing.qty if existing else 0) + delta
        if new_qty == 0:
            self._positions.pop(intent.symbol, None)
        else:
            self._positions[intent.symbol] = Position(
                symbol=intent.symbol,
                qty=new_qty,
                avg_entry_price=0.0,
                market_value=0.0,
            )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_execution_fake_broker.py -v`
Expected: PASS (4 passed).

- [ ] **Step 5: Commit**

```bash
git add tradingagents/execution/broker/fake.py tests/test_execution_fake_broker.py
git commit -m "feat(execution): add in-memory FakeBroker for tests"
```

---

### Task 4: position_policy.decide (rating → orders)

**Files:**
- Create: `tradingagents/execution/position_policy.py`
- Test: `tests/test_execution_position_policy.py`

**Interfaces:**
- Consumes: `OrderIntent`, `Position`, `Account` from `tradingagents/execution/broker/base.py`; `ExecutionConfig` from `tradingagents/execution/config.py`.
- Produces:
  `decide(symbol: str, rating: str, position: Position | None, account: Account, price: float, cfg: ExecutionConfig, cycle_id: str) -> list[OrderIntent]`.
  Returns `[]` for no action; one intent for a simple open/close/resize; two intents `[close, open]` for a reversal. `client_order_id` of leg *i* is `f"{symbol}:{cycle_id}:{i}"`. Target sizing: `Buy`=full long, `Overweight`=half long, `Hold`/unknown=no change, `Underweight`=half short, `Sell`=full short, where full dollars = `cfg.per_name_pct * account.equity`, half = `0.5 ×` full, and shares = `int(dollars // price)`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_execution_position_policy.py
import pytest

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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_execution_position_policy.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'tradingagents.execution.position_policy'`.

- [ ] **Step 3: Write minimal implementation**

```python
# tradingagents/execution/position_policy.py
from __future__ import annotations

from .broker.base import Account, OrderIntent, Position
from .config import ExecutionConfig


def _target_signed_shares(
    rating: str, equity: float, price: float, cfg: ExecutionConfig
) -> int | None:
    """Signed target share count for a rating, or None to leave the position unchanged."""
    if price <= 0:
        return None
    full = int((cfg.per_name_pct * equity) // price)
    half = int((0.5 * cfg.per_name_pct * equity) // price)
    return {
        "Buy": full,
        "Overweight": half,
        "Underweight": -half,
        "Sell": -full,
    }.get(rating)  # Hold / unknown -> None (no action)


def _is_reversal(current: int, target: int) -> bool:
    return current != 0 and target != 0 and (current > 0) != (target > 0)


def decide(
    symbol: str,
    rating: str,
    position: Position | None,
    account: Account,
    price: float,
    cfg: ExecutionConfig,
    cycle_id: str,
) -> list[OrderIntent]:
    """Map a rating + current position to the orders needed to reach the target."""
    current = position.qty if position else 0
    target = _target_signed_shares(rating, account.equity, price, cfg)
    if target is None or target == current:
        return []

    if _is_reversal(current, target):
        return [
            OrderIntent(
                symbol=symbol,
                side="sell" if current > 0 else "buy",
                qty=abs(current),
                client_order_id=f"{symbol}:{cycle_id}:0",
                reduce_only=True,
            ),
            OrderIntent(
                symbol=symbol,
                side="buy" if target > 0 else "sell",
                qty=abs(target),
                client_order_id=f"{symbol}:{cycle_id}:1",
                reduce_only=False,
            ),
        ]

    delta = target - current
    return [
        OrderIntent(
            symbol=symbol,
            side="buy" if delta > 0 else "sell",
            qty=abs(delta),
            client_order_id=f"{symbol}:{cycle_id}:0",
            reduce_only=abs(target) < abs(current),
        )
    ]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_execution_position_policy.py -v`
Expected: PASS (11 passed).

- [ ] **Step 5: Run the full suite + lint**

Run: `pytest -q && ruff check tradingagents/execution tests/test_execution_*.py`
Expected: all tests pass; ruff reports no errors.

- [ ] **Step 6: Commit**

```bash
git add tradingagents/execution/position_policy.py tests/test_execution_position_policy.py
git commit -m "feat(execution): add pure position_policy.decide rating-to-orders mapping"
```

---

## Self-Review

**1. Spec coverage (slice 1 only):**
- Spec §4 package layout (`config.py`, `broker/base.py`, `broker/fake.py`, `position_policy.py`) → Tasks 1–4. ✓
- Spec §4.1 interfaces (`OrderIntent`/`Position`/`Account`/`Broker`) → Task 2 (exact field names/types). ✓
- Spec §6 rating→order truth table, conviction sizing, reverse-in-one-cycle, list return, leg-indexed `client_order_id` → Task 4 tests cover every row incl. both reversals, trim, add, and no-op. ✓
- Spec §9 `TRADINGAGENTS_EXEC_*` typed env overrides, fail-loud → Task 1. ✓
- Spec §10 testing rule (no network/LLM, `FakeBroker`) → Task 3 + all tests are pure. ✓
- Deferred to later slices (correctly out of this plan): `AlpacaBroker` (slice 2), `screener`/`risk`/`loop` (slice 3), `ledger`/ops (slice 4). Noted, not gaps.

**2. Placeholder scan:** No TBD/TODO/"handle edge cases"/"similar to". Every code step shows complete code. ✓

**3. Type consistency:** `decide()` signature and return type match between Task 4's Interfaces block and its implementation; `OrderIntent`/`Position`/`Account` field names are identical across Tasks 2, 3, 4; `client_order_id` format `f"{symbol}:{cycle_id}:{leg}"` is consistent in Task 2 (comment), Task 3 (test data), and Task 4 (implementation + tests). `ExecutionConfig.per_name_pct` used in Task 4 matches Task 1's field. ✓
