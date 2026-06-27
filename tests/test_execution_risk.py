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
