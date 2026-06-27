from __future__ import annotations

STAGES: list[tuple[str, str]] = [
    ("market_report", "Market Analyst"),
    ("sentiment_report", "Social Analyst"),
    ("news_report", "News Analyst"),
    ("fundamentals_report", "Fundamentals Analyst"),
    ("investment_plan", "Research Manager"),
    ("trader_investment_plan", "Trader"),
    ("final_trade_decision", "Portfolio Manager"),
]


def events_from_state(state: dict, seen: set) -> list[dict]:
    """One ``stage_complete`` event per state key that just became non-empty."""
    out: list[dict] = []
    for key, stage in STAGES:
        if key not in seen and state.get(key):
            seen.add(key)
            out.append({"type": "stage_complete", "stage": stage, "text": state[key]})
    return out
