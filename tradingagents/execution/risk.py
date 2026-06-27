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
    gross_exposure: float = 0.0,
    equity: float = 0.0,
    notional_fn=None,
) -> list[OrderIntent]:
    """Filter intents through the kill switch, position cap, and gross-exposure cap.

    When ``notional_fn`` and ``equity`` are supplied, a non-``reduce_only`` intent
    is dropped if it would push running gross exposure above
    ``cfg.max_gross_exposure_pct * equity``; ``reduce_only`` intents always pass
    and free exposure. With ``notional_fn=None`` the gross check is skipped.
    """
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
