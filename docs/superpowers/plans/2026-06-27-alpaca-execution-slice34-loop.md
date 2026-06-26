# Alpaca Execution Loop — Slices 3+4 (Screener, Risk, Loop, Ledger) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn the manual `trade-once` into an autonomous daily cycle: screen a candidate universe by volatility, re-evaluate every name, enforce risk guardrails, place paper orders, and record everything to a P&L ledger.

**Architecture:** Four new modules in `tradingagents/execution/`, all pure or dependency-injected so the cycle tests with `FakeBroker` + stubs (no network/LLM). `screener` ranks symbols by realized volatility. `risk` is a pure filter (kill switch + max-concurrent-positions). `loop.run_cycle` orchestrates reconcile → screen → decide → risk → submit → record. `ledger` is an append-only JSONL of equity snapshots / orders / cycle summaries with a P&L readout.

**Tech Stack:** Python 3.10+, the slice-1/2 `execution` package, `yfinance` (price history), pytest.

## Global Constraints

- Python floor 3.10 — `X | None`, `list[...]`, `from __future__ import annotations`.
- Tests require no real API keys, network, or LLM calls: `FakeBroker` + stub `rating_fn` / `price_fn` / `screen_fn` / `calendar_fn`; ledger tests use `tmp_path`.
- Lint clean under `ruff check .` (strict select; `E501` ignored).
- Commit messages: conventional-commit prefix, no AI co-author trailer.
- Scoping (documented, not silent): the screener ranks a **caller-supplied** candidate list (the Alpaca-movers source is deferred); `risk` enforces the kill switch and `max_concurrent_positions` (gross-exposure cap deferred — logged in the loop, not silently ignored).

---

### Task 1: Volatility screener

**Files:**
- Create: `tradingagents/execution/screener.py`
- Test: `tests/test_execution_screener.py`

**Interfaces:**
- Produces: `realized_volatility(closes: list[float]) -> float` (population stdev of simple returns; `0.0` for <2 points) and `rank_by_volatility(symbols: list[str], history_fn, top_k: int) -> list[str]` (rank symbols by `realized_volatility(history_fn(symbol))` descending, return the top `top_k`). `history_fn(symbol) -> list[float]` is injected.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_execution_screener.py
import math

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
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_execution_screener.py -q`
Expected: FAIL — `No module named 'tradingagents.execution.screener'`.

- [ ] **Step 3: Implement**

```python
# tradingagents/execution/screener.py
from __future__ import annotations


def realized_volatility(closes: list[float]) -> float:
    """Population standard deviation of simple period-over-period returns."""
    if len(closes) < 2:
        return 0.0
    returns = [closes[i] / closes[i - 1] - 1.0 for i in range(1, len(closes))]
    mean = sum(returns) / len(returns)
    variance = sum((r - mean) ** 2 for r in returns) / len(returns)
    return variance ** 0.5


def rank_by_volatility(symbols: list[str], history_fn, top_k: int) -> list[str]:
    """Return the ``top_k`` symbols with the highest realized volatility."""
    scored = [(s, realized_volatility(history_fn(s))) for s in symbols]
    scored.sort(key=lambda pair: pair[1], reverse=True)
    return [s for s, _ in scored[:top_k]]
```

- [ ] **Step 4: Run to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_execution_screener.py -q`
Expected: PASS (4 passed).

- [ ] **Step 5: Commit**

```bash
git add tradingagents/execution/screener.py tests/test_execution_screener.py
git commit -m "feat(execution): add volatility screener (realized vol + rank top-k)"
```

---

### Task 2: Risk guardrails

**Files:**
- Create: `tradingagents/execution/risk.py`
- Test: `tests/test_execution_risk.py`

**Interfaces:**
- Consumes: `OrderIntent` from `broker/base.py`; `ExecutionConfig` from `config.py`.
- Produces:
  - `kill_switch_tripped(baseline_equity: float, current_equity: float, cfg: ExecutionConfig) -> bool` — True when the drawdown from `baseline_equity` meets/exceeds `cfg.daily_loss_limit_pct`.
  - `apply(intents: list[OrderIntent], *, held_symbols: set[str], cfg: ExecutionConfig, kill_switch: bool = False) -> list[OrderIntent]` — drops non-`reduce_only` intents when `kill_switch` is set (halt new entries, still allow risk-reducing closes) and caps the number of *new* symbols opened so the held count never exceeds `cfg.max_concurrent_positions`. Intents for already-held symbols and `reduce_only` intents always pass.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_execution_risk.py
from tradingagents.execution.broker.base import OrderIntent
from tradingagents.execution.config import ExecutionConfig
from tradingagents.execution.risk import apply, kill_switch_tripped

CFG = ExecutionConfig(max_concurrent_positions=2, daily_loss_limit_pct=0.03)


def _open(symbol):
    return OrderIntent(symbol, "buy", 10, f"{symbol}:c:0", reduce_only=False)


def _close(symbol):
    return OrderIntent(symbol, "sell", 10, f"{symbol}:c:0", reduce_only=True)


def test_kill_switch_trips_at_threshold():
    assert kill_switch_tripped(100_000, 97_000, CFG) is True   # -3.0% == limit
    assert kill_switch_tripped(100_000, 98_000, CFG) is False  # -2.0%


def test_kill_switch_ignores_zero_baseline():
    assert kill_switch_tripped(0, 0, CFG) is False


def test_kill_switch_halts_new_entries_keeps_closes():
    intents = [_open("AAPL"), _close("TSLA")]
    out = apply(intents, held_symbols={"TSLA"}, cfg=CFG, kill_switch=True)
    assert out == [_close("TSLA")]


def test_max_concurrent_positions_caps_new_symbols():
    # held has 1, max 2 -> capacity for 1 new symbol
    intents = [_open("AAPL"), _open("MSFT")]
    out = apply(intents, held_symbols={"NVDA"}, cfg=CFG)
    assert out == [_open("AAPL")]


def test_held_symbol_and_reduce_only_always_pass():
    intents = [_close("NVDA"), _open("NVDA")]  # reversal on a held symbol
    out = apply(intents, held_symbols={"NVDA"}, cfg=CFG)
    assert out == intents
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_execution_risk.py -q`
Expected: FAIL — `No module named 'tradingagents.execution.risk'`.

- [ ] **Step 3: Implement**

```python
# tradingagents/execution/risk.py
from __future__ import annotations

from .broker.base import OrderIntent
from .config import ExecutionConfig


def kill_switch_tripped(
    baseline_equity: float, current_equity: float, cfg: ExecutionConfig
) -> bool:
    """True when the drawdown from ``baseline_equity`` meets the daily loss limit."""
    if baseline_equity <= 0:
        return False
    drawdown = (baseline_equity - current_equity) / baseline_equity
    return drawdown >= cfg.daily_loss_limit_pct


def apply(
    intents: list[OrderIntent],
    *,
    held_symbols: set[str],
    cfg: ExecutionConfig,
    kill_switch: bool = False,
) -> list[OrderIntent]:
    """Filter intents through the kill switch and the max-concurrent-positions cap."""
    capacity = cfg.max_concurrent_positions - len(held_symbols)
    new_symbols: set[str] = set()
    out: list[OrderIntent] = []
    for intent in intents:
        if kill_switch and not intent.reduce_only:
            continue  # halt new entries; risk-reducing closes still pass
        opens_new = intent.symbol not in held_symbols and not intent.reduce_only
        if opens_new and intent.symbol not in new_symbols:
            if len(new_symbols) >= capacity:
                continue  # at the position cap; skip this new symbol
            new_symbols.add(intent.symbol)
        out.append(intent)
    return out
```

- [ ] **Step 4: Run to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_execution_risk.py -q`
Expected: PASS (5 passed).

- [ ] **Step 5: Commit**

```bash
git add tradingagents/execution/risk.py tests/test_execution_risk.py
git commit -m "feat(execution): add risk guardrails (kill switch + max-concurrent-positions)"
```

---

### Task 3: Ledger

**Files:**
- Create: `tradingagents/execution/ledger.py`
- Test: `tests/test_execution_ledger.py`

**Interfaces:**
- Consumes: `OrderIntent` from `broker/base.py`.
- Produces: `Ledger(path)` with `snapshot_equity(equity, date)`, `record_order(intent, order_id)`, `write_cycle_summary(date, summary: dict)`, `read() -> list[dict]`, and `pnl_summary() -> dict` (`{snapshots, first, last, pnl, return_pct}` from the equity snapshots). Append-only JSONL; parent dir created on init.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_execution_ledger.py
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
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_execution_ledger.py -q`
Expected: FAIL — `No module named 'tradingagents.execution.ledger'`.

- [ ] **Step 3: Implement**

```python
# tradingagents/execution/ledger.py
from __future__ import annotations

import json
from pathlib import Path

from .broker.base import OrderIntent


class Ledger:
    """Append-only JSONL record of equity snapshots, orders, and cycle summaries."""

    def __init__(self, path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def _append(self, record: dict) -> None:
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record) + "\n")

    def snapshot_equity(self, equity: float, date: str) -> None:
        self._append({"type": "equity", "date": date, "equity": equity})

    def record_order(self, intent: OrderIntent, order_id: str) -> None:
        self._append({
            "type": "order",
            "symbol": intent.symbol,
            "side": intent.side,
            "qty": intent.qty,
            "client_order_id": intent.client_order_id,
            "order_id": order_id,
        })

    def write_cycle_summary(self, date: str, summary: dict) -> None:
        self._append({"type": "cycle", "date": date, **summary})

    def read(self) -> list[dict]:
        if not self.path.exists():
            return []
        return [
            json.loads(line)
            for line in self.path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]

    def pnl_summary(self) -> dict:
        equities = [r["equity"] for r in self.read() if r["type"] == "equity"]
        if not equities:
            return {"snapshots": 0, "first": None, "last": None, "pnl": 0.0, "return_pct": 0.0}
        first, last = equities[0], equities[-1]
        pnl = last - first
        return {
            "snapshots": len(equities),
            "first": first,
            "last": last,
            "pnl": pnl,
            "return_pct": (pnl / first) if first else 0.0,
        }
```

- [ ] **Step 4: Run to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_execution_ledger.py -q`
Expected: PASS (3 passed).

- [ ] **Step 5: Commit**

```bash
git add tradingagents/execution/ledger.py tests/test_execution_ledger.py
git commit -m "feat(execution): add append-only JSONL ledger with P&L summary"
```

---

### Task 4: Daily cycle (loop) + CLI

**Files:**
- Create: `tradingagents/execution/loop.py`
- Test: `tests/test_execution_loop.py`

**Interfaces:**
- Consumes: `position_policy.decide`, `risk`, `ExecutionConfig`, `Broker`, `FakeBroker` (tests), `Ledger`.
- Produces:
  `run_cycle(date, *, cfg, broker, rating_fn, screen_fn, price_fn, calendar_fn=None, baseline_equity=None, ledger=None, cycle_id=None) -> dict`.
  Returns `{"status": "market_closed"}` when `calendar_fn(date)` is falsey. Otherwise: reconcile account + positions, snapshot equity to the ledger, compute the kill switch from `baseline_equity` (default = current equity → never trips first run), build `tickers = screen_fn() + held` (deduped, deterministic), decide intents per ticker via `position_policy.decide`, filter through `risk.apply`, submit each via the broker (recording to the ledger), and return a summary dict `{status, tickers, intents, orders, kill_switch}`. Plus `main(argv=None)` for `python -m tradingagents.execution.loop --tickers AAPL,NVDA`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_execution_loop.py
from tradingagents.execution.broker.base import OrderIntent
from tradingagents.execution.broker.fake import FakeBroker
from tradingagents.execution.config import ExecutionConfig
from tradingagents.execution.ledger import Ledger
from tradingagents.execution.loop import run_cycle

CFG = ExecutionConfig(per_name_pct=0.05, max_concurrent_positions=10, daily_loss_limit_pct=0.03)


def _cycle(broker, *, screen, rating, baseline=None, ledger=None, calendar=None):
    return run_cycle(
        "2026-06-27",
        cfg=CFG,
        broker=broker,
        rating_fn=lambda ticker, date: rating.get(ticker, "Hold"),
        screen_fn=lambda: screen,
        price_fn=lambda symbol: 100.0,
        baseline_equity=baseline,
        ledger=ledger,
        calendar_fn=calendar,
    )


def test_market_closed_does_nothing():
    broker = FakeBroker()
    summary = _cycle(broker, screen=["AAPL"], rating={"AAPL": "Buy"}, calendar=lambda d: False)
    assert summary["status"] == "market_closed"
    assert broker.submitted == []


def test_buy_on_screened_name_opens_position():
    broker = FakeBroker(equity=100_000.0, cash=100_000.0)
    summary = _cycle(broker, screen=["AAPL"], rating={"AAPL": "Buy"})
    assert summary["orders"] == 1
    assert broker.get_position("AAPL").qty == 50


def test_held_name_is_re_evaluated_even_if_not_screened():
    broker = FakeBroker(equity=100_000.0, cash=100_000.0)
    broker.submit_order(OrderIntent("AAPL", "buy", 50, "seed:0"))  # pre-existing long
    summary = _cycle(broker, screen=[], rating={"AAPL": "Sell"})  # flip to short
    assert broker.get_position("AAPL").qty == -50  # reversed in one cycle
    assert summary["orders"] == 2  # close + open


def test_kill_switch_halts_new_entries():
    broker = FakeBroker(equity=100_000.0, cash=100_000.0)
    # baseline far above current equity -> drawdown beyond the 3% limit
    summary = _cycle(broker, screen=["AAPL"], rating={"AAPL": "Buy"}, baseline=200_000.0)
    assert summary["kill_switch"] is True
    assert summary["orders"] == 0
    assert broker.submitted == []


def test_ledger_records_equity_and_orders(tmp_path):
    broker = FakeBroker(equity=100_000.0, cash=100_000.0)
    led = Ledger(tmp_path / "ledger.jsonl")
    _cycle(broker, screen=["AAPL"], rating={"AAPL": "Buy"}, ledger=led)
    types = [r["type"] for r in led.read()]
    assert "equity" in types and "order" in types and "cycle" in types
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_execution_loop.py -q`
Expected: FAIL — `No module named 'tradingagents.execution.loop'`.

- [ ] **Step 3: Implement**

```python
# tradingagents/execution/loop.py
from __future__ import annotations

import argparse
import logging

from . import position_policy, risk
from .broker.base import Broker
from .config import ExecutionConfig
from .ledger import Ledger

logger = logging.getLogger(__name__)


def run_cycle(
    date: str,
    *,
    cfg: ExecutionConfig,
    broker: Broker,
    rating_fn,
    screen_fn,
    price_fn,
    calendar_fn=None,
    baseline_equity: float | None = None,
    ledger: Ledger | None = None,
    cycle_id: str | None = None,
) -> dict:
    """Run one autonomous trading cycle: screen -> re-evaluate -> risk -> submit."""
    cycle_id = cycle_id or f"{date}#1"
    if calendar_fn is not None and not calendar_fn(date):
        logger.info("Market closed on %s; nothing to do.", date)
        return {"status": "market_closed", "tickers": 0, "intents": 0, "orders": 0,
                "kill_switch": False}

    account = broker.account()
    held = {p.symbol for p in broker.positions()}
    baseline = baseline_equity if baseline_equity is not None else account.equity
    if ledger is not None:
        ledger.snapshot_equity(account.equity, date)

    tripped = risk.kill_switch_tripped(baseline, account.equity, cfg)
    if tripped:
        logger.warning(
            "Daily-loss kill switch tripped (baseline=%.2f current=%.2f); halting new entries.",
            baseline, account.equity,
        )
    # Gross-exposure cap (cfg.max_gross_exposure_pct) is not yet enforced here.
    if cfg.max_gross_exposure_pct < 1.0:
        logger.info("max_gross_exposure_pct=%.2f is configured but not yet enforced.",
                    cfg.max_gross_exposure_pct)

    # Always re-evaluate held names, even when the screen doesn't surface them.
    tickers = list(dict.fromkeys(list(screen_fn()) + sorted(held)))

    all_intents = []
    for ticker in tickers:
        rating = rating_fn(ticker, date)
        price = price_fn(ticker)
        position = broker.get_position(ticker)
        all_intents.extend(
            position_policy.decide(ticker, rating, position, account, price, cfg, cycle_id)
        )

    allowed = risk.apply(all_intents, held_symbols=held, cfg=cfg, kill_switch=tripped)
    orders = 0
    for intent in allowed:
        order_id = broker.submit_order(intent)
        if ledger is not None:
            ledger.record_order(intent, order_id)
        orders += 1

    summary = {
        "status": "ok",
        "tickers": len(tickers),
        "intents": len(all_intents),
        "orders": orders,
        "kill_switch": tripped,
    }
    if ledger is not None:
        ledger.write_cycle_summary(date, summary)
    logger.info(
        "Cycle %s: %d tickers, %d intents, %d orders%s",
        date, len(tickers), len(all_intents), orders, " (kill switch)" if tripped else "",
    )
    return summary


def main(argv: list[str] | None = None) -> None:
    import datetime
    import os

    from tradingagents.dataflows.symbol_utils import normalize_symbol

    from .screener import rank_by_volatility
    from .trade_once import _default_broker, _default_price, _default_rating

    parser = argparse.ArgumentParser(
        prog="trading-loop",
        description="Run one autonomous trading cycle: screen by volatility, re-evaluate, place paper orders.",
    )
    parser.add_argument("--tickers", required=True,
                        help="Comma-separated candidate universe to screen by volatility.")
    parser.add_argument("--date", default=datetime.datetime.now().strftime("%Y-%m-%d"))
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    cfg = ExecutionConfig.from_env()
    candidates = [t.strip().upper() for t in args.tickers.split(",") if t.strip()]

    def history_fn(symbol: str) -> list[float]:
        import yfinance as yf
        return yf.Ticker(normalize_symbol(symbol)).history(period="1mo")["Close"].tolist()

    ledger_path = os.path.join(
        os.path.expanduser("~"), ".tradingagents", "execution", "ledger.jsonl"
    )
    run_cycle(
        args.date,
        cfg=cfg,
        broker=_default_broker(cfg),
        rating_fn=_default_rating,
        screen_fn=lambda: rank_by_volatility(candidates, history_fn, cfg.top_k),
        price_fn=_default_price,
        ledger=Ledger(ledger_path),
    )


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_execution_loop.py -q`
Expected: PASS (5 passed).

- [ ] **Step 5: Full suite + lint**

Run: `.venv/bin/python -m pytest -q && .venv/bin/python -m ruff check tradingagents/execution tests/test_execution_*.py`
Expected: all pass (slice 1/2 tests + 4 screener + 5 risk + 3 ledger + 5 loop = 17 new); ruff clean.

- [ ] **Step 6: Commit**

```bash
git add tradingagents/execution/loop.py tests/test_execution_loop.py
git commit -m "feat(execution): add autonomous daily run_cycle + trading-loop CLI"
```

---

## Self-Review

**1. Spec coverage:**
- Spec §5 data flow (reconcile → screen → decide → risk → submit → summary) → Task 4 `run_cycle`. ✓
- Spec §5 "always re-evaluate held names" → `tickers = screen + held`, tested. ✓
- Spec §7 kill switch (halt-only) + max-concurrent-positions → Task 2 `risk`, tested. Gross-exposure cap explicitly deferred + logged (documented scoping). ✓ (partial, flagged)
- Spec §8 ledger (equity/orders/cycle, P&L, never drives decisions) → Task 3. ✓
- Spec "dynamic volatility screen" → Task 1 ranks by realized vol; the Alpaca-movers candidate *source* is deferred (caller supplies the universe via `--tickers`), documented. ✓ (partial, flagged)
- Spec §6 reuse of `position_policy.decide` and the `Broker` seam → Task 4. ✓

**2. Placeholder scan:** No TBD/TODO in code. The two deferrals (gross-exposure enforcement, Alpaca-movers source) are explicit, logged, and documented here — not silent gaps.

**3. Type consistency:** `run_cycle` calls `position_policy.decide(ticker, rating, position, account, price, cfg, cycle_id)` (slice-1 signature) and `risk.apply(intents, held_symbols=, cfg=, kill_switch=)` (Task 2 signature); `risk.kill_switch_tripped` and `ExecutionConfig` fields (`daily_loss_limit_pct`, `max_concurrent_positions`, `max_gross_exposure_pct`, `per_name_pct`, `top_k`) all match slice-1 `config.py`. `Ledger` method names match between Task 3 and Task 4 (`snapshot_equity`, `record_order`, `write_cycle_summary`). `FakeBroker(equity=, cash=)` matches slice 1.
