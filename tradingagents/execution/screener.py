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


def fetch_alpaca_candidates(client, *, top: int = 20, include_movers: bool = True) -> list[str]:
    """Most-active stocks (and optionally top movers) as a deduped candidate list."""
    from alpaca.data.requests import MarketMoversRequest, MostActivesRequest

    symbols: list[str] = [
        s.symbol for s in client.get_most_actives(MostActivesRequest(top=top)).most_actives
    ]
    if include_movers:
        movers = client.get_market_movers(MarketMoversRequest(top=top))
        symbols.extend(m.symbol for m in [*movers.gainers, *movers.losers])
    seen: set[str] = set()
    out: list[str] = []
    for s in symbols:
        if s not in seen:
            seen.add(s)
            out.append(s)
    return out


def _default_screener_client():
    import os

    from alpaca.data.historical.screener import ScreenerClient

    return ScreenerClient(os.environ.get("ALPACA_API_KEY"), os.environ.get("ALPACA_SECRET_KEY"))
