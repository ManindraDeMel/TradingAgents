from __future__ import annotations


def realized_volatility(closes: list[float]) -> float:
    """Population standard deviation of simple period-over-period returns."""
    if len(closes) < 2:
        return 0.0
    returns = [closes[i] / closes[i - 1] - 1.0 for i in range(1, len(closes))]
    mean = sum(returns) / len(returns)
    variance = sum((r - mean) ** 2 for r in returns) / len(returns)
    return variance ** 0.5


def rank_by_volatility(symbols: list[str], history_fn, top_k: int) -> list[str]:
    """Return the ``top_k`` symbols with the highest realized volatility."""
    scored = [(s, realized_volatility(history_fn(s))) for s in symbols]
    scored.sort(key=lambda pair: pair[1], reverse=True)
    return [s for s, _ in scored[:top_k]]
