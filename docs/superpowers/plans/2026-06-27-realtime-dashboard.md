# Real-time Run-View Dashboard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans. Steps use `- [ ]`.

**Goal:** A local web app that streams the agent pipeline live ("what it's up to" + each agent's reasoning as it lands), shows the final decision + the paper order it places, and a portfolio side panel.

**Architecture:** New `tradingagents/execution/dashboard/` package behind a `[dashboard]` extra (Flask). Pure, testable backend: `events.events_from_state` (graph state → stage events), `run.run_events` (drives a chunk source → streams events → decides → submits → emits order), `viewmodel.portfolio_view` (broker + ledger → side-panel dict). `app.create_app(run_fn, portfolio_fn)` exposes `/`, `/api/portfolio`, and an SSE `/stream/<ticker>`. The real chunk source reuses `TradingAgentsGraph`'s public pieces (`propagator`, `resolve_instrument_context`, `memory_log`, `graph.stream`) — **no core graph changes**. Frontend (`templates/index.html` + `static/`) implements the "The Desk" design and renders the SSE stream. Design tokens: ink `#14171C` / panel `#1B1F26` / bone `#ECE6D8` text / amber `#E8A33D` active-pulse / sage `#6FB8A0` long / clay `#C8654B` short; Space Grotesk (display) + IBM Plex Mono (reasoning/data) + IBM Plex Sans (body), via Google Fonts.

**Tech Stack:** Python 3.10+, Flask (optional `[dashboard]`), the `execution` package, pytest + Flask test client.

## Global Constraints

- No network/keys/LLM in tests: `events`/`viewmodel` are pure; `run_events` and routes are tested with a fake chunk source + `FakeBroker` + stub price; SSE tested via Flask's test client.
- Flask imported lazily / only inside the dashboard package; the `[dashboard]` extra keeps core installs lean (bare `import tradingagents` must not need Flask).
- `from __future__ import annotations`; ruff clean (`E501` ignored); conventional commits, no AI co-author trailer.
- Quality floor (frontend): responsive, visible focus, `prefers-reduced-motion` respected.

---

### Task 1: Portfolio view-model

**Files:** Create `tradingagents/execution/dashboard/__init__.py` (empty), `tradingagents/execution/dashboard/viewmodel.py`; Test `tests/test_dashboard_viewmodel.py`.

**Interfaces:** `portfolio_view(broker, ledger=None) -> dict` → `{equity, cash, buying_power, is_paper, positions:[{symbol,qty,market_value,avg_entry_price}], recent_orders:[...≤10], pnl:{...}}`.

- [ ] **Step 1: failing test**

```python
# tests/test_dashboard_viewmodel.py
from tradingagents.execution.broker.base import OrderIntent
from tradingagents.execution.broker.fake import FakeBroker
from tradingagents.execution.dashboard.viewmodel import portfolio_view
from tradingagents.execution.ledger import Ledger


def test_portfolio_view_from_broker_and_ledger(tmp_path):
    broker = FakeBroker(equity=5000.0, cash=5000.0)
    broker.submit_order(OrderIntent("AAPL", "buy", 3, "AAPL:c:0"))
    led = Ledger(tmp_path / "l.jsonl")
    led.snapshot_equity(5000.0, "2026-06-26")
    led.record_order(OrderIntent("AAPL", "buy", 3, "AAPL:c:0"), "o1")
    view = portfolio_view(broker, led)
    assert view["equity"] == 5000.0 and view["is_paper"] is True
    assert view["positions"][0]["symbol"] == "AAPL" and view["positions"][0]["qty"] == 3
    assert view["recent_orders"][-1]["order_id"] == "o1"
    assert view["pnl"]["snapshots"] == 1


def test_portfolio_view_without_ledger():
    view = portfolio_view(FakeBroker())
    assert view["recent_orders"] == [] and view["positions"] == []
```

- [ ] **Step 2:** `.venv/bin/python -m pytest tests/test_dashboard_viewmodel.py -q` → fails (module missing).

- [ ] **Step 3: implement**

```python
# tradingagents/execution/dashboard/viewmodel.py
from __future__ import annotations


def portfolio_view(broker, ledger=None) -> dict:
    account = broker.account()
    rows = ledger.read() if ledger is not None else []
    return {
        "equity": account.equity,
        "cash": account.cash,
        "buying_power": account.buying_power,
        "is_paper": getattr(broker, "is_paper", True),
        "positions": [
            {"symbol": p.symbol, "qty": p.qty, "market_value": p.market_value,
             "avg_entry_price": p.avg_entry_price}
            for p in broker.positions()
        ],
        "recent_orders": [r for r in rows if r.get("type") == "order"][-10:],
        "pnl": ledger.pnl_summary() if ledger is not None else
               {"snapshots": 0, "first": None, "last": None, "pnl": 0.0, "return_pct": 0.0},
    }
```

- [ ] **Step 4:** rerun → pass (2).
- [ ] **Step 5: commit** `feat(dashboard): add portfolio view-model`.

---

### Task 2: Pipeline events + run stream

**Files:** Create `tradingagents/execution/dashboard/events.py`, `tradingagents/execution/dashboard/run.py`; Test `tests/test_dashboard_events.py`.

**Interfaces:**
- `STAGES: list[tuple[str,str]]` (state-key, display-name) and `events_from_state(state: dict, seen: set) -> list[dict]` — emits `{"type":"stage_complete","stage":...,"text":...}` for each newly-populated key.
- `run_events(ticker, date, *, cfg, broker, chunk_source, price_fn, ledger=None, cycle_id=None) -> Iterator[dict]` — yields `run_started`, the stage events, `decision` (rating+text), then `order`/`no_order`, then `done`. `chunk_source(ticker, date)` yields `("chunk", state)` … `("final", state)`.

- [ ] **Step 1: failing test**

```python
# tests/test_dashboard_events.py
from tradingagents.execution.broker.fake import FakeBroker
from tradingagents.execution.config import ExecutionConfig
from tradingagents.execution.dashboard.events import events_from_state
from tradingagents.execution.dashboard.run import run_events


def test_events_from_state_emits_each_key_once():
    seen = set()
    e1 = events_from_state({"market_report": "m"}, seen)
    assert e1 == [{"type": "stage_complete", "stage": "Market Analyst", "text": "m"}]
    # same key again -> nothing; new key -> one event
    assert events_from_state({"market_report": "m"}, seen) == []
    e2 = events_from_state({"market_report": "m", "news_report": "n"}, seen)
    assert e2 == [{"type": "stage_complete", "stage": "News Analyst", "text": "n"}]


def test_run_events_buy_streams_then_orders():
    def chunk_source(ticker, date):
        yield ("chunk", {"market_report": "ok"})
        yield ("chunk", {"market_report": "ok", "final_trade_decision": "Rating: Buy\nlong it"})
        yield ("final", {"final_trade_decision": "Rating: Buy\nlong it"})

    broker = FakeBroker(equity=100_000.0, cash=100_000.0)
    events = list(run_events(
        "AAPL", "2026-06-27",
        cfg=ExecutionConfig(per_name_pct=0.05),
        broker=broker, chunk_source=chunk_source, price_fn=lambda s: 100.0,
    ))
    types = [e["type"] for e in events]
    assert types[0] == "run_started" and types[-1] == "done"
    assert "stage_complete" in types
    decision = next(e for e in events if e["type"] == "decision")
    assert decision["rating"] == "Buy"
    order = next(e for e in events if e["type"] == "order")
    assert order["side"] == "buy" and order["qty"] == 50
    assert broker.get_position("AAPL").qty == 50


def test_run_events_hold_emits_no_order():
    def chunk_source(t, d):
        yield ("final", {"final_trade_decision": "Rating: Hold"})
    events = list(run_events("AAPL", "2026-06-27", cfg=ExecutionConfig(),
                             broker=FakeBroker(), chunk_source=chunk_source, price_fn=lambda s: 100.0))
    assert any(e["type"] == "no_order" for e in events)
    assert not any(e["type"] == "order" for e in events)
```

- [ ] **Step 2:** run → fails (modules missing).

- [ ] **Step 3: implement events.py**

```python
# tradingagents/execution/dashboard/events.py
from __future__ import annotations

STAGES: list[tuple[str, str]] = [
    ("market_report", "Market Analyst"),
    ("sentiment_report", "Social Analyst"),
    ("news_report", "News Analyst"),
    ("fundamentals_report", "Fundamentals Analyst"),
    ("investment_plan", "Research Manager"),
    ("trader_investment_plan", "Trader"),
    ("final_trade_decision", "Portfolio Manager"),
]


def events_from_state(state: dict, seen: set) -> list[dict]:
    """One ``stage_complete`` event per state key that just became non-empty."""
    out: list[dict] = []
    for key, stage in STAGES:
        if key not in seen and state.get(key):
            seen.add(key)
            out.append({"type": "stage_complete", "stage": stage, "text": state[key]})
    return out
```

- [ ] **Step 4: implement run.py**

```python
# tradingagents/execution/dashboard/run.py
from __future__ import annotations

import logging
from collections.abc import Iterator

from tradingagents.agents.utils.rating import parse_rating

from .. import position_policy
from ..config import ExecutionConfig
from .events import STAGES, events_from_state

logger = logging.getLogger(__name__)


def run_events(
    ticker: str,
    date: str,
    *,
    cfg: ExecutionConfig,
    broker,
    chunk_source,
    price_fn,
    ledger=None,
    cycle_id: str | None = None,
) -> Iterator[dict]:
    """Stream pipeline-stage events, then decide + submit the paper order."""
    cycle_id = cycle_id or f"{date}#1"
    stage_names = [name for _, name in STAGES]
    yield {"type": "run_started", "ticker": ticker, "date": date, "stages": stage_names}

    seen: set = set()
    final_state: dict = {}
    for kind, payload in chunk_source(ticker, date):
        if kind == "chunk":
            for ev in events_from_state(payload, seen):
                yield ev
        elif kind == "final":
            final_state = payload

    decision_text = final_state.get("final_trade_decision", "")
    rating = parse_rating(decision_text)
    yield {"type": "decision", "rating": rating, "text": decision_text}

    price = price_fn(ticker)
    account = broker.account()
    position = broker.get_position(ticker)
    intents = position_policy.decide(ticker, rating, position, account, price, cfg, cycle_id)
    if not intents:
        yield {"type": "no_order", "reason": f"target matches current position (rating={rating})"}
    for intent in intents:
        order_id = broker.submit_order(intent)
        if ledger is not None:
            ledger.record_order(intent, order_id)
        yield {"type": "order", "side": intent.side, "symbol": intent.symbol,
               "qty": intent.qty, "order_id": order_id, "client_order_id": intent.client_order_id}
    yield {"type": "done"}


def default_chunk_source(ticker: str, date: str):
    """Real chunk source: stream the live graph (reuses public TradingAgentsGraph pieces)."""
    from tradingagents.default_config import DEFAULT_CONFIG
    from tradingagents.graph.trading_graph import TradingAgentsGraph

    ta = TradingAgentsGraph(config=DEFAULT_CONFIG.copy())
    past = ta.memory_log.get_past_context(ticker)
    instrument = ta.resolve_instrument_context(ticker, "stock")
    state = ta.propagator.create_initial_state(
        ticker, date, asset_type="stock", past_context=past, instrument_context=instrument
    )
    args = ta.propagator.get_graph_args()
    final: dict = {}
    for chunk in ta.graph.stream(state, **args):
        final.update(chunk)
        yield ("chunk", chunk)
    yield ("final", final)
```

- [ ] **Step 5:** run → pass (3). **Commit** `feat(dashboard): add pipeline event stream + run_events`.

---

### Task 3: Flask app (routes + SSE)

**Files:** Create `tradingagents/execution/dashboard/app.py`; Test `tests/test_dashboard_app.py`. Modify `pyproject.toml` (`[dashboard]` extra, console script, package-data).

**Interfaces:** `create_app(*, run_fn, portfolio_fn) -> Flask` with `/` (HTML), `/api/portfolio` (JSON), `/stream/<ticker>` (SSE of `run_fn(ticker, date)`); `main()` builds the real wiring and serves.

- [ ] **Step 1:** add `[dashboard]` extra `flask>=3.0`, then `.venv/bin/python -m pip install -e ".[dashboard]"`.

- [ ] **Step 2: failing test**

```python
# tests/test_dashboard_app.py
import json

import pytest

pytest.importorskip("flask")

from tradingagents.execution.dashboard.app import create_app


def _client():
    def run_fn(ticker, date):
        yield {"type": "run_started", "ticker": ticker}
        yield {"type": "decision", "rating": "Buy", "text": "x"}
        yield {"type": "done"}

    app = create_app(run_fn=run_fn, portfolio_fn=lambda: {"equity": 5000.0, "positions": []})
    app.config.update(TESTING=True)
    return app.test_client()


def test_index_serves_html():
    r = _client().get("/")
    assert r.status_code == 200 and b"<!doctype html" in r.data.lower()


def test_portfolio_endpoint_returns_json():
    r = _client().get("/api/portfolio")
    assert r.status_code == 200 and r.get_json()["equity"] == 5000.0


def test_stream_emits_sse_events():
    r = _client().get("/stream/AAPL")
    assert r.status_code == 200
    body = r.get_data(as_text=True)
    assert "event-stream" in r.headers["Content-Type"]
    payloads = [json.loads(line[5:]) for line in body.splitlines() if line.startswith("data:")]
    assert payloads[0]["type"] == "run_started" and payloads[-1]["type"] == "done"
```

- [ ] **Step 3: implement app.py** (the real `main()` wires `default_chunk_source` + `AlpacaBroker` + `Ledger`):

```python
# tradingagents/execution/dashboard/app.py
from __future__ import annotations

import datetime
import json


def create_app(*, run_fn, portfolio_fn):
    from flask import Flask, Response, jsonify, render_template, request

    app = Flask(__name__)

    @app.route("/")
    def index():
        return render_template("index.html")

    @app.route("/api/portfolio")
    def portfolio():
        return jsonify(portfolio_fn())

    @app.route("/stream/<ticker>")
    def stream(ticker):
        date = request.args.get("date") or datetime.date.today().isoformat()

        def gen():
            for event in run_fn(ticker.upper(), date):
                yield f"data: {json.dumps(event)}\n\n"

        return Response(gen(), mimetype="text/event-stream")

    return app


def main(argv: list[str] | None = None) -> None:
    import argparse
    import logging

    from tradingagents.dataflows.symbol_utils import normalize_symbol

    from ..config import ExecutionConfig
    from ..ledger import Ledger
    from ..trade_once import _default_broker, _default_price
    from .run import default_chunk_source, run_events
    from .viewmodel import portfolio_view

    parser = argparse.ArgumentParser(prog="trading-dashboard")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    import os
    cfg = ExecutionConfig.from_env()
    broker = _default_broker(cfg)
    ledger = Ledger(os.path.join(os.path.expanduser("~"), ".tradingagents", "execution", "ledger.jsonl"))

    def run_fn(ticker, date):
        yield from run_events(ticker, date, cfg=cfg, broker=broker,
                              chunk_source=default_chunk_source, price_fn=_default_price, ledger=ledger)

    app = create_app(run_fn=run_fn, portfolio_fn=lambda: portfolio_view(broker, ledger))
    print(f"  trading-dashboard -> http://{args.host}:{args.port}")
    app.run(host=args.host, port=args.port, threaded=True)


if __name__ == "__main__":
    main()
```

- [ ] **Step 4:** run → pass (3). **Commit** (app + pyproject) `feat(dashboard): add Flask app with SSE run stream`.

---

### Task 4: Frontend ("The Desk") + console script + docs

**Files:** Create `templates/index.html`, `static/app.css`, `static/app.js` under the dashboard package; modify `pyproject.toml` package-data + `[project.scripts]`; create `docs/dashboard.md`.

- [ ] **Step 1:** Build the page to the design tokens (relay rail / reasoning stream / decision+trade / portfolio panel). Ticker input + Run button opens `EventSource('/stream/'+ticker)`; render `run_started` (build relay from `stages`), `stage_complete` (mark done, pulse next, stream text), `decision`, `order`/`no_order`, `done`; poll `/api/portfolio` every 5s. Respect `prefers-reduced-motion`.
- [ ] **Step 2:** `[project.scripts]` += `trading-dashboard = "tradingagents.execution.dashboard.app:main"`; package-data += `"tradingagents.execution.dashboard" = ["templates/*", "static/*"]`. Reinstall.
- [ ] **Step 3: render check** — serve `create_app` with a fake run_fn + sample portfolio on a port, screenshot with headless Chrome (`/Applications/Google Chrome.app/Contents/MacOS/Google Chrome --headless --screenshot`), review, iterate on CSS.
- [ ] **Step 4:** `docs/dashboard.md` (setup, `trading-dashboard`, what it shows).
- [ ] **Step 5: full suite + lint.** **Commit** `feat(dashboard): add real-time run-view frontend + console script + docs`.

---

## Self-Review

1. **Coverage:** real-time progress (SSE stage events) ✓; thought process (each agent's report streamed as `stage_complete.text`) ✓ (per-agent granularity; token-level deferred, noted); trades + portfolio (viewmodel + Alpaca/ledger) ✓; design via tokens ✓; screenshot verification ✓.
2. **Placeholders:** none; token-level streaming explicitly deferred.
3. **Type consistency:** `run_events` consumes `position_policy.decide(...)` (slice-1 sig) + `parse_rating` (agents.utils.rating); `viewmodel` reads slice-1 `Position`/`Account` + `Ledger.read/pnl_summary`; `create_app(run_fn, portfolio_fn)` matches `main`'s wiring and the tests. No core graph changes — `default_chunk_source` only reads public `TradingAgentsGraph` attributes.
