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
