# Real-time Run-View Dashboard ("The Desk")

A local web app that streams the agent pipeline live — you watch each specialist
activate, read its reasoning as it lands, then see the final decision and the
paper order it places, alongside a portfolio panel.

## Setup

```bash
pip install -e ".[dashboard]"     # adds Flask
```

Needs the same `.env` as the loop (`ALPACA_API_KEY`/`ALPACA_SECRET_KEY` for the
portfolio panel + order placement, and an LLM provider — e.g.
`TRADINGAGENTS_LLM_PROVIDER=anthropic` with `ANTHROPIC_API_KEY`).

## Run

```bash
trading-dashboard                 # serves http://127.0.0.1:8765
trading-dashboard --port 9000     # custom port
```

Open the URL, type a ticker, and hit **Run analysis**. The page connects to a
Server-Sent Events stream and renders:

- **Pipeline rail** — each specialist as a station; the active one pulses amber,
  finished ones turn sage with their output captured.
- **Thought process** — the active agent's reasoning streams in as it completes.
- **Decision + trade** — the final 5-tier rating and the long/short paper order
  (or "no order" with the reason).
- **Portfolio** — live Alpaca equity, positions, and recent orders (polled).

## Notes

- **Granularity:** v1 streams at per-agent resolution (each specialist's output
  as it lands). Token-by-token "typing" of the raw reasoning is a future
  enhancement.
- **Paper-only** by default — same gating as the rest of the execution layer.
- Each run also persists to the usual places (`full_states_log` JSON, the memory
  log, and — via the order path — the execution ledger).
