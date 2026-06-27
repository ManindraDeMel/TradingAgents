# Autonomous Execution Loop (Alpaca paper)

Runs the multi-agent pipeline across a volatile-stock universe once per trading
day and places the matching long/short orders on an Alpaca **paper** account.

## Setup

```bash
pip install -e ".[alpaca]"
```

In `.env` (gitignored):

```bash
ALPACA_API_KEY=...          # paper keys: app.alpaca.markets -> Paper Trading -> API Keys
ALPACA_SECRET_KEY=...
ANTHROPIC_API_KEY=...        # or any provider; + TRADINGAGENTS_LLM_PROVIDER etc.
# Optional execution tuning (defaults shown):
# TRADINGAGENTS_EXEC_PER_NAME_PCT=0.05
# TRADINGAGENTS_EXEC_MAX_CONCURRENT_POSITIONS=10
# TRADINGAGENTS_EXEC_MAX_GROSS_EXPOSURE_PCT=1.0
# TRADINGAGENTS_EXEC_DAILY_LOSS_LIMIT_PCT=0.03
# TRADINGAGENTS_EXEC_TOP_K=10
```

## Run

```bash
trading-loop                       # auto-screen Alpaca most-actives + movers
trading-loop --tickers NVDA,COIN   # or supply your own universe
trading-loop --ignore-calendar     # run even on a non-trading day (testing)
```

Each run snapshots equity, screens the universe by realized volatility (top-K),
re-evaluates each name (plus current holdings), maps ratings to long/short
orders, applies the kill switch + position + gross-exposure caps, submits to
Alpaca paper, and appends to `~/.tradingagents/execution/ledger.jsonl`.

**Paper-only by default.** Live trading requires `allow_live` and is intentionally gated.

## Schedule with cron

US equities close at 16:00 ET. Run shortly after close on weekdays; the loop
self-skips holidays via the Alpaca calendar, so a Mon–Fri schedule is safe.
`cron` uses the machine's local timezone — adjust the hour to your TZ.

```cron
# 16:30 America/New_York, Mon-Fri (set CRON_TZ if your box isn't on ET)
CRON_TZ=America/New_York
30 16 * * 1-5 cd /path/to/TradingAgents && /path/to/TradingAgents/.venv/bin/trading-loop >> ~/.tradingagents/execution/loop.log 2>&1
```

Inspect results: tail `~/.tradingagents/execution/loop.log`, or read the ledger
(`~/.tradingagents/execution/ledger.jsonl`) for equity snapshots, orders, and
cycle summaries.
