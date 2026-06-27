import json

import pytest

pytest.importorskip("flask")

from tradingagents.execution.dashboard.app import create_app


def _client():
    def run_fn(ticker, date):
        yield {"type": "run_started", "ticker": ticker}
        yield {"type": "decision", "rating": "Buy", "text": "x"}
        yield {"type": "done"}

    app = create_app(run_fn=run_fn, portfolio_fn=lambda: {"equity": 5000.0, "positions": []})
    app.config.update(TESTING=True)
    return app.test_client()


def test_index_serves_html():
    r = _client().get("/")
    assert r.status_code == 200 and b"<!doctype html" in r.data.lower()


def test_portfolio_endpoint_returns_json():
    r = _client().get("/api/portfolio")
    assert r.status_code == 200 and r.get_json()["equity"] == 5000.0


def test_stream_emits_sse_events():
    r = _client().get("/stream/AAPL")
    assert r.status_code == 200
    body = r.get_data(as_text=True)
    assert "event-stream" in r.headers["Content-Type"]
    payloads = [json.loads(line[5:]) for line in body.splitlines() if line.startswith("data:")]
    assert payloads[0]["type"] == "run_started" and payloads[-1]["type"] == "done"
