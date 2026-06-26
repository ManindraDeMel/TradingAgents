# Alpaca Autonomous Execution Loop — Design Spec

**Date:** 2026-06-27
**Status:** Approved (design); pending implementation plan
**Branch:** `feature/alpaca-execution-loop`

## 1. Summary

Add an autonomous paper-trading loop that runs the existing `TradingAgentsGraph`
decision engine against a dynamically screened universe of volatile stocks once
per trading day, translates each agent decision into long/short orders on
Alpaca's **paper** API, manages those positions by daily re-evaluation, and
tracks paper P&L. The horizon is short-term (days within a week), not intraday.

The decision engine is **not modified**. Everything is built as a new sibling
package, `tradingagents/execution/`, that consumes the graph through its existing
`propagate()` seam.

## 2. Goals / Non-goals

### Goals
- Turn the final 5-tier rating (`Buy / Overweight / Hold / Underweight / Sell`)
  into managed long/short paper positions on Alpaca.
- Run autonomously on a daily cadence with no human in the loop.
- Build a daily universe by screening for volatility.
- Re-evaluate held names daily and reverse/close as conviction changes.
- Enforce risk guardrails and a daily-loss kill switch.
- Keep the whole layer testable with **zero** network or LLM calls in tests.

### Non-goals (explicitly out of scope for this spec)
- Intraday / minute-level trading.
- Live (real-money) trading — supported only behind an explicit, deliberate opt-in.
- Backtesting over historical windows (the loop is forward-only; the existing
  reflection layer already grades realized outcomes).
- Portfolio optimization beyond the simple sizing/caps described here.
- A UI/dashboard (P&L is file-based; reporting can come later).

## 3. Current state (what we build on)

A run today ends at a **recommendation**, not a trade:

- `TradingAgentsGraph.propagate(ticker, date)` returns `(final_state, rating)`.
- `graph/signal_processing.py::SignalProcessor.process_signal` →
  `agents/utils/rating.py::parse_rating` extracts one of the 5-tier ratings.
- A grep for `alpaca|broker|submit_order|place_order` finds **no execution code**;
  the README's "simulated exchange / order executed" language is narrative only.
- The append-only memory log (`agents/utils/memory.py`) stores each decision as
  `pending` and, on the **next run of the same ticker**, resolves the prior
  decision's realized return + alpha and writes a reflection. Daily re-evaluation
  feeds this for free.

The clean seam (final rating) is where the execution layer attaches.

## 4. Architecture (Approach A: new execution package)

```
tradingagents/execution/
  __init__.py
  config.py          # ExecutionConfig dataclass, populated from TRADINGAGENTS_EXEC_* env
  broker/
    __init__.py
    base.py          # Broker protocol + OrderIntent / Position / Account dataclasses
    alpaca.py        # AlpacaBroker (alpaca-py); paper endpoint hard-defaulted
    fake.py          # FakeBroker: in-memory Broker impl for tests
  screener.py        # build_universe(): movers/most-actives -> volatility rank -> top-K
  position_policy.py # decide(rating, position, account, cfg) -> list[OrderIntent]  (PURE)
  risk.py            # apply(intent, portfolio_state, cfg) -> OrderIntent | None; kill switch
  ledger.py          # append-only JSONL of intents/fills/equity snapshots
  loop.py            # run_cycle(); __main__ CLI entry
```

**Design properties**
- The decision engine is consumed as a black box; no edits to `graph/` or `agents/`.
- `Broker` is a Protocol; `AlpacaBroker` and `FakeBroker` are interchangeable
  implementations. This mirrors the repo's existing "abstract interface → adapter"
  patterns (dataflows vendor layer, LLM provider factory), so a future
  IBKR/sim broker drops in without touching callers.
- `position_policy.decide` is a pure function — no I/O, no clock, no network —
  so its full behavior is a unit-test matrix.

### 4.1 Key interfaces (illustrative)

```python
# broker/base.py
@dataclass(frozen=True)
class OrderIntent:
    symbol: str
    side: str               # "buy" | "sell"
    qty: int                # whole shares (>0)
    client_order_id: str    # idempotency key: f"{symbol}:{date}:{cycle}:{leg}"
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

class Broker(Protocol):
    is_paper: bool
    def account(self) -> Account: ...
    def positions(self) -> list[Position]: ...
    def get_position(self, symbol: str) -> Position | None: ...
    def submit_order(self, intent: OrderIntent) -> str: ...   # returns broker order id
    def cancel_all(self) -> None: ...
    def close_position(self, symbol: str) -> str | None: ...
```

## 5. Data flow — one daily cycle

```
run_cycle(graph_factory, broker, cfg):
  0. GUARD   market calendar open today? kill switch tripped for the session?
             -> if not tradable, log and exit cleanly.
  1. RECONCILE  account = broker.account(); positions = broker.positions()
                ledger.snapshot_equity(account)   # establishes day-open baseline
  2. SCREEN  candidates = screener.build_universe(cfg)         # movers -> vol rank -> top-K
             tickers = unique(candidates ∪ {held symbols})     # always re-evaluate holdings
  3. DECIDE  for ticker in tickers:
               _, rating = graph_factory().propagate(ticker, today)   # full agent run
               intents = position_policy.decide(rating, get_position(ticker), account, cfg)
               intents = risk.apply(intents, portfolio_state, cfg)     # cap / scale / skip
               for intent in intents:                                 # 0, 1, or 2 (reversal)
                   order_id = broker.submit_order(intent)
                   ledger.record(intent, order_id, rating)
  4. SUMMARY  ledger.write_cycle_summary(decisions, fills, equity)
```

Notes:
- Held names are always included in step 2, guaranteeing daily re-evaluation and
  triggering the memory/reflection resolution of their prior decision.
- A `NO_DATA_AVAILABLE`/rating-less run yields no intent (treated as Hold) — the
  loop never trades on a fabricated value.

## 6. Position policy (rating → order)

Long/short, conviction-weighted. "Full" = `per_name_pct × equity`; "half" = `0.5 ×` full.
Dollar target → whole shares at the latest price.

| Rating | Flat | Long | Short |
|---|---|---|---|
| **Buy**         | open long, full  | hold              | close + open long, full   |
| **Overweight**  | open long, half  | hold              | close → long, half        |
| **Hold**        | nothing          | hold              | hold                      |
| **Underweight** | open short, half | close → short, half | hold                    |
| **Sell**        | open short, full | close + open short, full | hold                |

**Flip behavior:** reverse in one cycle (long→Sell closes the long *and* opens a
full short in the same cycle), driven by agent conviction. Confirmed decision.

`decide()` returns a **list** of `OrderIntent` (empty = no action, one = simple
open/close/resize, two = reversal). A reversal returns `[close existing, open
opposite]` and both legs are submitted in the same cycle, so reverse-in-one-cycle
holds regardless of whether the broker supports crossing zero in a single order.
Each leg gets a distinct `client_order_id` (`…:{cycle}:{leg}`). The invariant: after
the cycle, the held signed position equals the policy's target.

## 7. Risk guardrails & safety

- **Paper-only by default.** `AlpacaBroker.__init__` targets the paper endpoint.
  Live requires BOTH `ExecutionConfig.allow_live=True` AND an explicit `--live`
  CLI flag; neither alone enables it. `is_paper` is asserted at loop start unless
  live is deliberately enabled.
- **Position caps:** `max_concurrent_positions`, `per_name_pct` of equity,
  `max_gross_exposure_pct`. `risk.apply` scales an intent down or skips it when a
  cap would be breached.
- **Daily-loss kill switch:** if intraday equity falls below
  `daily_loss_limit_pct` from the day-open snapshot, **halt new entries** for the
  remainder of the session (default). Flatten-on-breach is a config option
  (`kill_switch_flatten=False` by default). Confirmed: halt-only default.
- **Idempotency:** every order carries
  `client_order_id = f"{symbol}:{date}:{cycle}:{leg}"` (the `leg` ordinal keeps a
  reversal's two legs distinct) so a crash-and-rerun within a cycle cannot
  double-submit.
- **Fail-safe screening:** if the screener errors, the loop still re-evaluates
  existing holdings (risk management continues) and logs that no new candidates
  were added — it does not open new positions on stale data.

## 8. State & persistence

- **Source of truth:** Alpaca for positions and cash. The loop reconciles from
  the broker at the start of every cycle; it never trusts local state for what is
  held.
- **Ledger:** append-only JSONL under `~/.tradingagents/execution/` recording
  intents, submitted order ids, fills (polled/reconciled next cycle), and equity
  snapshots — for P&L attribution and audit only. It never drives decisions, so a
  lost/corrupt ledger cannot desync the broker.
- **Reflection:** unchanged. The existing memory log grades each ticker's prior
  decision on its next run; the daily loop supplies that cadence automatically.

## 9. Configuration & dependencies

- **New dependency:** `alpaca-py`, added as an **optional extra** so the core
  install stays lean (same pattern as the existing `[bedrock]` extra):
  `pip install ".[alpaca]"`.
- **Credentials:** `ALPACA_API_KEY`, `ALPACA_SECRET_KEY` (paper keys). Added to
  `.env.example`.
- **Config:** a new `ExecutionConfig` dataclass with `TRADINGAGENTS_EXEC_*` env
  overrides (cadence, top-K, `per_name_pct`, `max_concurrent_positions`,
  `max_gross_exposure_pct`, `daily_loss_limit_pct`, `kill_switch_flatten`,
  `allow_live`). Follows the existing `_ENV_OVERRIDES` philosophy: typed coercion,
  fail loud on invalid values.
- **Scheduling:** out-of-process. A CLI entry (`python -m tradingagents.execution.loop`)
  is fired once per trading day by cron/launchd; the loop itself checks the market
  calendar and exits cleanly on non-trading days. No always-on daemon.

## 10. Testing strategy

Holds the repo rule that tests need no real API keys, network, or LLM calls.

- **`position_policy`** — exhaustive matrix: every rating × every current-position
  state (flat/long/short) × sizing (full/half) × cap interactions.
- **`risk`** — boundary tests for each cap and the kill switch (above/below
  threshold; halt-only vs flatten).
- **`screener`** — volatility ranking against fixture OHLCV; deterministic top-K.
- **`loop.run_cycle`** — driven by `FakeBroker` (in-memory) + a stubbed
  `graph_factory` returning scripted ratings; asserts the exact orders submitted,
  reversal behavior, kill-switch halting, and idempotency on re-run.
- **`AlpacaBroker`** — unit-tested against a mocked `alpaca-py` client (request
  shape, paper-endpoint assertion, live-guard); no live calls. Any real
  paper-account test is marked `integration` and skipped without keys.

## 11. Build sequence (vertical slices)

Each slice is independently shippable and testable.

1. **Testable core (no network):** `broker/base.py` (protocol + dataclasses),
   `broker/fake.py`, `position_policy.py`, `config.py` + their tests. Provable
   order logic with zero external dependencies.
2. **Real paper fills:** `broker/alpaca.py` + a one-shot `trade-once <ticker>`
   command that runs one real `propagate` → one paper order. Proves the
   integration end to end.
3. **Autonomous cycle:** `screener.py`, `risk.py`, `loop.run_cycle` + CLI entry.
   The full daily loop.
4. **Reporting & ops:** `ledger.py` P&L summary + cron/launchd setup docs and
   `.env.example` / README updates.

## 12. Open questions / future work

- Order type for entries (market-on-open vs limit) — default market-next-open;
  revisit if slippage on volatile names is material.
- Fill reconciliation timing (poll at cycle start vs intra-cycle) — start with
  next-cycle reconciliation; the ledger tolerates eventual consistency.
- Partial-fill and rejected-order handling policy — log and re-evaluate next
  cycle; no mid-cycle retry loop in v1.
- Live-trading enablement is intentionally deferred and gated; not in this scope.
