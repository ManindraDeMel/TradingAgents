from __future__ import annotations


def portfolio_view(broker, ledger=None) -> dict:
    """Side-panel view-model: account, positions, recent orders, and P&L."""
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
