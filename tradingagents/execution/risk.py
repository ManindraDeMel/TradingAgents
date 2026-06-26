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
