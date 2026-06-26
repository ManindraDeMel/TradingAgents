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
