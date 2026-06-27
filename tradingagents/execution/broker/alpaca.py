from __future__ import annotations

from .base import Account, OrderIntent, Position


class AlpacaBroker:
    """Broker implementation backed by alpaca-py's TradingClient.

    alpaca-py is imported lazily so this module imports without the optional
    ``[alpaca]`` extra installed; only constructing a real client or submitting
    an order pulls it in. Tests inject a fake ``client``.
    """

    def __init__(
        self,
        client=None,
        *,
        api_key: str | None = None,
        secret_key: str | None = None,
        paper: bool = True,
        allow_live: bool = False,
    ) -> None:
        if not paper and not allow_live:
            raise ValueError(
                "Refusing to construct a live AlpacaBroker without allow_live=True. "
                "Paper trading is the default; pass allow_live=True to trade real money."
            )
        self.is_paper = paper
        if client is None:
            from alpaca.trading.client import TradingClient

            client = TradingClient(api_key, secret_key, paper=paper)
        self._client = client

    def account(self) -> Account:
        a = self._client.get_account()
        return Account(
            equity=float(a.equity),
            cash=float(a.cash),
            buying_power=float(a.buying_power),
        )

    def positions(self) -> list[Position]:
        return [self._to_position(p) for p in self._client.get_all_positions()]

    def get_position(self, symbol: str) -> Position | None:
        try:
            return self._to_position(self._client.get_open_position(symbol))
        except Exception:
            # alpaca-py raises when there is no open position for the symbol.
            return None

    def submit_order(self, intent: OrderIntent) -> str:
        from alpaca.trading.enums import OrderSide, TimeInForce
        from alpaca.trading.requests import MarketOrderRequest

        req = MarketOrderRequest(
            symbol=intent.symbol,
            qty=intent.qty,
            side=OrderSide.BUY if intent.side == "buy" else OrderSide.SELL,
            time_in_force=TimeInForce.DAY,
            client_order_id=intent.client_order_id,
        )
        order = self._client.submit_order(order_data=req)
        return str(order.id)

    def cancel_all(self) -> None:
        self._client.cancel_orders()

    def close_position(self, symbol: str) -> str | None:
        try:
            order = self._client.close_position(symbol)
        except Exception:
            return None
        return str(getattr(order, "id", "")) or None

    def is_trading_day(self, date) -> bool:
        import datetime as _dt

        from alpaca.trading.requests import GetCalendarRequest

        day = _dt.date.fromisoformat(date) if isinstance(date, str) else date
        calendar = self._client.get_calendar(GetCalendarRequest(start=day, end=day))
        return len(calendar) > 0

    @staticmethod
    def _to_position(p) -> Position:
        from alpaca.trading.enums import PositionSide

        qty = abs(int(float(p.qty)))
        signed = -qty if p.side == PositionSide.SHORT else qty
        return Position(
            symbol=p.symbol,
            qty=signed,
            avg_entry_price=float(p.avg_entry_price),
            market_value=float(p.market_value),
        )
