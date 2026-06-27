# Alpaca Execution Loop — Deferrals + Cron Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the two documented deferrals (Alpaca-movers candidate source, `max_gross_exposure_pct` enforcement) and make the loop cron-ready (console-script entry, trading-day guard, ops docs).

**Architecture:** Extend `risk.apply` with an optional gross-exposure gate (injected `notional_fn` + current gross + equity), wired from `run_cycle`. Add `screener.fetch_alpaca_candidates(client, ...)` (lazy `alpaca-py` import, injectable client) and `AlpacaBroker.is_trading_day(date)`. Make `loop.main` auto-screen Alpaca most-actives + movers when `--tickers` is omitted and skip non-trading days. Add `trading-loop` / `trade-once` console scripts and an ops doc.

**Tech Stack:** Python 3.10+, the `execution` package, `alpaca-py` (optional `[alpaca]`), `yfinance`, pytest.

## Global Constraints

- Python floor 3.10; `from __future__ import annotations`; ruff clean (`E501` ignored).
- Tests require no network/keys/LLM: risk + loop tests use stubs/`FakeBroker`; Alpaca-touching tests `importorskip` and inject a `MagicMock` client.
- `alpaca-py` stays optional + lazily imported (bare `import` of the package must not require it).
- Backward compatibility: `risk.apply`'s new params are optional with defaults (existing slice-3 tests must still pass unchanged).
- Commit messages: conventional-commit prefix, no AI co-author trailer.
- Verified alpaca-py API: `TradingClient.get_calendar(GetCalendarRequest(start=date, end=date))` → `list[Calendar]` (empty ⇒ not a trading day); `ScreenerClient(api_key, secret_key)`; `get_most_actives(MostActivesRequest(top=N))` → `.most_actives` (each `.symbol`); `get_market_movers(MarketMoversRequest(top=N))` → `.gainers` / `.losers` (each `.symbol`). Request classes live in `alpaca.data.requests`; `ScreenerClient` in `alpaca.data.historical.screener`.

---

### Task 1: Enforce max_gross_exposure_pct

**Files:**
- Modify: `tradingagents/execution/risk.py` (extend `apply`)
- Modify: `tradingagents/execution/loop.py` (compute + pass gross/notional)
- Test: `tests/test_execution_risk.py` (add cases), `tests/test_execution_loop.py` (add a loop case)

**Interfaces:**
- `apply(intents, *, held_symbols, cfg, kill_switch=False, gross_exposure=0.0, equity=0.0, notional_fn=None)` — when `notional_fn` and `equity > 0`, a non-`reduce_only` intent is dropped if it would push running gross exposure above `cfg.max_gross_exposure_pct * equity`; `reduce_only` intents always pass and *reduce* the running total. With `notional_fn=None` behavior is unchanged.
- `run_cycle` computes `gross_exposure = sum(abs(p.market_value) for p in positions)` and `notional_fn = lambda intent: intent.qty * price_by_symbol[intent.symbol]`, passing both plus `equity=account.equity` to `apply`.

- [ ] **Step 1: Add failing risk tests**

Append to `tests/test_execution_risk.py`:

```python
def _open_n(symbol):
    return OrderIntent(symbol, "buy", 10, f"{symbol}:c:0", reduce_only=False)


def test_gross_exposure_cap_skips_intents_over_limit():
    cfg = ExecutionConfig(max_concurrent_positions=10, max_gross_exposure_pct=0.10)
    intents = [_open_n("A"), _open_n("B"), _open_n("C")]  # each $5k notional, $10k limit
    out = apply(intents, held_symbols=set(), cfg=cfg, equity=100_000.0,
                gross_exposure=0.0, notional_fn=lambda i: 5000.0)
    assert out == [_open_n("A"), _open_n("B")]


def test_gross_exposure_counts_existing_exposure():
    cfg = ExecutionConfig(max_concurrent_positions=10, max_gross_exposure_pct=0.10)
    out = apply([_open_n("A")], held_symbols=set(), cfg=cfg, equity=100_000.0,
                gross_exposure=6_000.0, notional_fn=lambda i: 5000.0)  # 6k+5k > 10k
    assert out == []


def test_reduce_only_bypasses_gross_cap():
    cfg = ExecutionConfig(max_gross_exposure_pct=0.01)
    closes = [OrderIntent("A", "sell", 10, "A:c:0", reduce_only=True)]
    out = apply(closes, held_symbols={"A"}, cfg=cfg, equity=100_000.0,
                gross_exposure=50_000.0, notional_fn=lambda i: 5000.0)
    assert out == closes
```

- [ ] **Step 2: Run — expect 3 failures (TypeError: unexpected keyword 'gross_exposure')**

Run: `.venv/bin/python -m pytest tests/test_execution_risk.py -q`
Expected: the 3 new tests fail; the original 5 still pass.

- [ ] **Step 3: Extend `risk.apply`**

Replace the body of `apply` in `tradingagents/execution/risk.py` with:

```python
def apply(
    intents: list[OrderIntent],
    *,
    held_symbols: set[str],
    cfg: ExecutionConfig,
    kill_switch: bool = False,
    gross_exposure: float = 0.0,
    equity: float = 0.0,
    notional_fn=None,
) -> list[OrderIntent]:
    """Filter intents through the kill switch, position cap, and gross-exposure cap."""
    capacity = cfg.max_concurrent_positions - len(held_symbols)
    limit = cfg.max_gross_exposure_pct * equity if (notional_fn and equity > 0) else None
    used = gross_exposure
    new_symbols: set[str] = set()
    out: list[OrderIntent] = []
    for intent in intents:
        if kill_switch and not intent.reduce_only:
            continue  # halt new entries; risk-reducing closes still pass
        if intent.reduce_only:
            if limit is not None:
                used -= notional_fn(intent)  # closing frees exposure
            out.append(intent)
            continue
        is_new_symbol = intent.symbol not in held_symbols and intent.symbol not in new_symbols
        if is_new_symbol and len(new_symbols) >= capacity:
            continue  # at the position cap
        if limit is not None and used + notional_fn(intent) > limit:
            continue  # would breach gross-exposure cap
        if limit is not None:
            used += notional_fn(intent)
        if is_new_symbol:
            new_symbols.add(intent.symbol)
        out.append(intent)
    return out
```

- [ ] **Step 4: Run risk tests — expect pass (8)**

Run: `.venv/bin/python -m pytest tests/test_execution_risk.py -q`
Expected: 8 passed.

- [ ] **Step 5: Wire gross exposure into `run_cycle`**

In `tradingagents/execution/loop.py`, inside `run_cycle`, after computing `held` add a `positions` capture and gross total, build a price cache in the decide loop, and pass them to `apply`. Concretely:

- Capture positions once: change `held = {p.symbol for p in broker.positions()}` to
  ```python
  positions = broker.positions()
  held = {p.symbol for p in positions}
  gross_exposure = sum(abs(p.market_value) for p in positions)
  ```
- In the decide loop, record the price per ticker: add `prices: dict[str, float] = {}` before the loop and `prices[ticker] = price` after fetching `price`.
- Replace the `risk.apply(...)` call with:
  ```python
  allowed = risk.apply(
      all_intents,
      held_symbols=held,
      cfg=cfg,
      kill_switch=tripped,
      gross_exposure=gross_exposure,
      equity=account.equity,
      notional_fn=lambda intent: intent.qty * prices[intent.symbol],
  )
  ```
- Remove the now-obsolete "not yet enforced" log line for `max_gross_exposure_pct`.

- [ ] **Step 6: Add a loop-level gross-exposure test**

Append to `tests/test_execution_loop.py`:

```python
def test_gross_exposure_cap_limits_orders():
    broker = FakeBroker(equity=100_000.0, cash=100_000.0)
    cfg = ExecutionConfig(per_name_pct=0.05, max_concurrent_positions=10, max_gross_exposure_pct=0.10)
    summary = run_cycle(
        "2026-06-27",
        cfg=cfg,
        broker=broker,
        rating_fn=lambda ticker, date: "Buy",
        screen_fn=lambda: ["A", "B", "C"],   # each $5k notional, $10k limit -> 2 fit
        price_fn=lambda symbol: 100.0,
    )
    assert summary["orders"] == 2
```

- [ ] **Step 7: Run loop tests + lint**

Run: `.venv/bin/python -m pytest tests/test_execution_loop.py tests/test_execution_risk.py -q && .venv/bin/python -m ruff check tradingagents/execution/risk.py tradingagents/execution/loop.py`
Expected: pass (6 loop + 8 risk); ruff clean.

- [ ] **Step 8: Commit**

```bash
git add tradingagents/execution/risk.py tradingagents/execution/loop.py tests/test_execution_risk.py tests/test_execution_loop.py
git commit -m "feat(execution): enforce max_gross_exposure_pct in risk + loop"
```

---

### Task 2: Alpaca candidate source + trading-day guard

**Files:**
- Modify: `tradingagents/execution/screener.py` (add `fetch_alpaca_candidates`, `_default_screener_client`)
- Modify: `tradingagents/execution/broker/alpaca.py` (add `is_trading_day`)
- Modify: `tradingagents/execution/loop.py` (`main`: auto-screen + calendar guard)
- Test: `tests/test_execution_screener.py` (add), `tests/test_execution_alpaca_broker.py` (add)

**Interfaces:**
- `fetch_alpaca_candidates(client, *, top: int = 20, include_movers: bool = True) -> list[str]` — most-actives plus (optionally) gainers+losers, deduped, order-preserving. Lazy alpaca import; `client` injected.
- `_default_screener_client()` — builds `ScreenerClient` from `ALPACA_API_KEY`/`ALPACA_SECRET_KEY`.
- `AlpacaBroker.is_trading_day(date: str | date) -> bool` — True when the Alpaca calendar has an entry for `date`.
- `loop.main` — `--tickers` becomes optional (omit ⇒ auto-screen Alpaca); `--ignore-calendar` bypasses the trading-day guard.

- [ ] **Step 1: Add failing screener test**

Append to `tests/test_execution_screener.py`:

```python
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest


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
```

- [ ] **Step 2: Run — expect fail (ImportError of fetch_alpaca_candidates)**

Run: `.venv/bin/python -m pytest tests/test_execution_screener.py -q`
Expected: the new test errors on import; the 4 originals pass.

- [ ] **Step 3: Implement in `screener.py`**

Append to `tradingagents/execution/screener.py`:

```python
def fetch_alpaca_candidates(client, *, top: int = 20, include_movers: bool = True) -> list[str]:
    """Most-active stocks (and optionally top movers) as a deduped candidate list."""
    from alpaca.data.requests import MarketMoversRequest, MostActivesRequest

    symbols: list[str] = [s.symbol for s in client.get_most_actives(MostActivesRequest(top=top)).most_actives]
    if include_movers:
        movers = client.get_market_movers(MarketMoversRequest(top=top))
        symbols.extend(m.symbol for m in [*movers.gainers, *movers.losers])
    seen: set[str] = set()
    out: list[str] = []
    for s in symbols:
        if s not in seen:
            seen.add(s)
            out.append(s)
    return out


def _default_screener_client():
    import os

    from alpaca.data.historical.screener import ScreenerClient

    return ScreenerClient(os.environ.get("ALPACA_API_KEY"), os.environ.get("ALPACA_SECRET_KEY"))
```

- [ ] **Step 4: Run screener tests — expect pass (5)**

Run: `.venv/bin/python -m pytest tests/test_execution_screener.py -q`
Expected: 5 passed.

- [ ] **Step 5: Add failing is_trading_day test**

Append to `tests/test_execution_alpaca_broker.py` (the file already `importorskip`s alpaca at module top):

```python
def test_is_trading_day_true_when_calendar_nonempty():
    client = MagicMock()
    client.get_calendar.return_value = [SimpleNamespace(date="2026-06-26")]
    assert AlpacaBroker(client=client, paper=True).is_trading_day("2026-06-26") is True


def test_is_trading_day_false_when_calendar_empty():
    client = MagicMock()
    client.get_calendar.return_value = []
    assert AlpacaBroker(client=client, paper=True).is_trading_day("2026-06-27") is False
```

- [ ] **Step 6: Implement `is_trading_day` on `AlpacaBroker`**

Add this method to `AlpacaBroker` in `tradingagents/execution/broker/alpaca.py` (after `close_position`):

```python
    def is_trading_day(self, date) -> bool:
        import datetime as _dt

        from alpaca.trading.requests import GetCalendarRequest

        day = _dt.date.fromisoformat(date) if isinstance(date, str) else date
        calendar = self._client.get_calendar(GetCalendarRequest(start=day, end=day))
        return len(calendar) > 0
```

- [ ] **Step 7: Run adapter tests — expect pass (10)**

Run: `.venv/bin/python -m pytest tests/test_execution_alpaca_broker.py -q`
Expected: 10 passed.

- [ ] **Step 8: Wire auto-screen + calendar guard into `loop.main`**

Replace the body of `main` in `tradingagents/execution/loop.py` with:

```python
def main(argv: list[str] | None = None) -> None:
    import datetime
    import os

    from tradingagents.dataflows.symbol_utils import normalize_symbol

    from .screener import _default_screener_client, fetch_alpaca_candidates, rank_by_volatility
    from .trade_once import _default_broker, _default_price, _default_rating

    parser = argparse.ArgumentParser(
        prog="trading-loop",
        description="Run one autonomous trading cycle: screen by volatility, re-evaluate, place paper orders.",
    )
    parser.add_argument("--tickers", default=None,
                        help="Comma-separated universe; omit to auto-screen Alpaca most-actives + movers.")
    parser.add_argument("--date", default=datetime.datetime.now().strftime("%Y-%m-%d"))
    parser.add_argument("--ignore-calendar", action="store_true",
                        help="Run even if today is not a trading day.")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    cfg = ExecutionConfig.from_env()
    broker = _default_broker(cfg)

    def history_fn(symbol: str) -> list[float]:
        import yfinance as yf
        return yf.Ticker(normalize_symbol(symbol)).history(period="1mo")["Close"].tolist()

    if args.tickers:
        candidates = [t.strip().upper() for t in args.tickers.split(",") if t.strip()]

        def candidates_fn():
            return candidates
    else:
        screener_client = _default_screener_client()

        def candidates_fn():
            return fetch_alpaca_candidates(screener_client, top=max(cfg.top_k * 3, 20))

    ledger_path = os.path.join(
        os.path.expanduser("~"), ".tradingagents", "execution", "ledger.jsonl"
    )
    run_cycle(
        args.date,
        cfg=cfg,
        broker=broker,
        rating_fn=_default_rating,
        screen_fn=lambda: rank_by_volatility(candidates_fn(), history_fn, cfg.top_k),
        price_fn=_default_price,
        calendar_fn=None if args.ignore_calendar else broker.is_trading_day,
        ledger=Ledger(ledger_path),
    )
```

- [ ] **Step 9: Full suite + lint**

Run: `.venv/bin/python -m pytest -q && .venv/bin/python -m ruff check tradingagents/execution tests/test_execution_*.py`
Expected: all pass; ruff clean.

- [ ] **Step 10: Commit**

```bash
git add tradingagents/execution/screener.py tradingagents/execution/broker/alpaca.py tradingagents/execution/loop.py tests/test_execution_screener.py tests/test_execution_alpaca_broker.py
git commit -m "feat(execution): auto-screen Alpaca movers + trading-day guard"
```

---

### Task 3: Cron wiring (console scripts + ops docs)

**Files:**
- Modify: `pyproject.toml` (`[project.scripts]`)
- Create: `docs/execution-loop.md`

**Interfaces:** `trading-loop` and `trade-once` console commands; an ops doc with a sample crontab.

- [ ] **Step 1: Add console scripts**

In `pyproject.toml`, replace the `[project.scripts]` block:

```toml
[project.scripts]
tradingagents = "cli.main:app"
trading-loop = "tradingagents.execution.loop:main"
trade-once = "tradingagents.execution.trade_once:main"
```

- [ ] **Step 2: Reinstall so the entry points register**

Run: `.venv/bin/python -m pip install -e ".[dev,alpaca]" >/dev/null 2>&1; .venv/bin/trading-loop --help | head -5`
Expected: argparse help for `trading-loop` prints (confirms the entry point resolves).

- [ ] **Step 3: Write the ops doc**

Create `docs/execution-loop.md`:

````markdown
# Autonomous Execution Loop (Alpaca paper)

Runs the multi-agent pipeline across a volatile-stock universe once per trading
day and places the matching long/short orders on an Alpaca **paper** account.

## Setup

```bash
pip install -e ".[alpaca]"
```

In `.env` (gitignored):

```bash
ALPACA_API_KEY=...          # paper keys: app.alpaca.markets -> Paper Trading -> API Keys
ALPACA_SECRET_KEY=...
ANTHROPIC_API_KEY=...        # or any provider; + TRADINGAGENTS_LLM_PROVIDER etc.
# Optional execution tuning (defaults shown):
# TRADINGAGENTS_EXEC_PER_NAME_PCT=0.05
# TRADINGAGENTS_EXEC_MAX_CONCURRENT_POSITIONS=10
# TRADINGAGENTS_EXEC_MAX_GROSS_EXPOSURE_PCT=1.0
# TRADINGAGENTS_EXEC_DAILY_LOSS_LIMIT_PCT=0.03
# TRADINGAGENTS_EXEC_TOP_K=10
```

## Run

```bash
trading-loop                       # auto-screen Alpaca most-actives + movers
trading-loop --tickers NVDA,COIN   # or supply your own universe
trading-loop --ignore-calendar     # run even on a non-trading day (testing)
```

Each run snapshots equity, screens the universe by realized volatility (top-K),
re-evaluates each name (plus current holdings), maps ratings to long/short
orders, applies the kill switch + position + gross-exposure caps, submits to
Alpaca paper, and appends to `~/.tradingagents/execution/ledger.jsonl`.

**Paper-only by default.** Live trading requires `allow_live` and is intentionally gated.

## Schedule with cron

US equities close at 16:00 ET. Run shortly after close on weekdays; the loop
self-skips holidays via the Alpaca calendar, so a Mon–Fri schedule is safe.
`cron` uses the machine's local timezone — adjust the hour to your TZ.

```cron
# 16:30 America/New_York, Mon-Fri (set CRON_TZ if your box isn't on ET)
CRON_TZ=America/New_York
30 16 * * 1-5 cd /path/to/TradingAgents && /path/to/TradingAgents/.venv/bin/trading-loop >> ~/.tradingagents/execution/loop.log 2>&1
```

Inspect results: tail `~/.tradingagents/execution/loop.log`, or read the ledger
(`~/.tradingagents/execution/ledger.jsonl`) for equity snapshots, orders, and
cycle summaries.
````

- [ ] **Step 4: Commit**

```bash
git add pyproject.toml docs/execution-loop.md
git commit -m "feat(execution): add trading-loop/trade-once console scripts + ops docs"
```

---

## Self-Review

**1. Spec coverage:**
- Deferral — `max_gross_exposure_pct` now enforced in `risk.apply` (notional-aware) and wired from `run_cycle`. Task 1. ✓
- Deferral — Alpaca most-actives/movers candidate source (`fetch_alpaca_candidates`), default-on in `main` when `--tickers` omitted. Task 2. ✓
- Cron wiring — `trading-loop` console script, Alpaca trading-day guard (`is_trading_day`) wired as `calendar_fn`, ops doc with crontab. Tasks 2 + 3. ✓

**2. Placeholder scan:** No TBD/TODO. The obsolete "not yet enforced" log line for gross exposure is removed in Task 1 Step 5.

**3. Type consistency:** `apply`'s new keyword-only params are optional (slice-3 callers/tests unaffected). `run_cycle` passes `notional_fn`/`gross_exposure`/`equity` matching the new `apply` signature. `fetch_alpaca_candidates` + `_default_screener_client` + `AlpacaBroker.is_trading_day` names match between impl, `loop.main`, and tests. Console-script targets (`tradingagents.execution.loop:main`, `...trade_once:main`) match the existing `main()` functions.
