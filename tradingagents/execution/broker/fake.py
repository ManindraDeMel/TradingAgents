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
