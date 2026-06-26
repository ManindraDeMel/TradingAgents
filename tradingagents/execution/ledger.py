from __future__ import annotations

import json
from pathlib import Path

from .broker.base import OrderIntent


class Ledger:
    """Append-only JSONL record of equity snapshots, orders, and cycle summaries."""

    def __init__(self, path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def _append(self, record: dict) -> None:
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record) + "\n")

    def snapshot_equity(self, equity: float, date: str) -> None:
        self._append({"type": "equity", "date": date, "equity": equity})

    def record_order(self, intent: OrderIntent, order_id: str) -> None:
        self._append({
            "type": "order",
            "symbol": intent.symbol,
            "side": intent.side,
            "qty": intent.qty,
            "client_order_id": intent.client_order_id,
            "order_id": order_id,
        })

    def write_cycle_summary(self, date: str, summary: dict) -> None:
        self._append({"type": "cycle", "date": date, **summary})

    def read(self) -> list[dict]:
        if not self.path.exists():
            return []
        return [
            json.loads(line)
            for line in self.path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]

    def pnl_summary(self) -> dict:
        equities = [r["equity"] for r in self.read() if r["type"] == "equity"]
        if not equities:
            return {"snapshots": 0, "first": None, "last": None, "pnl": 0.0, "return_pct": 0.0}
        first, last = equities[0], equities[-1]
        pnl = last - first
        return {
            "snapshots": len(equities),
            "first": first,
            "last": last,
            "pnl": pnl,
            "return_pct": (pnl / first) if first else 0.0,
        }
