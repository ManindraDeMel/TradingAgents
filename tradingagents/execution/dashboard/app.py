from __future__ import annotations

import datetime
import json


def create_app(*, run_fn, portfolio_fn):
    from flask import Flask, Response, jsonify, render_template, request

    app = Flask(__name__)

    @app.route("/")
    def index():
        return render_template("index.html")

    @app.route("/api/portfolio")
    def portfolio():
        return jsonify(portfolio_fn())

    @app.route("/stream/<ticker>")
    def stream(ticker):
        date = request.args.get("date") or datetime.date.today().isoformat()

        def gen():
            for event in run_fn(ticker.upper(), date):
                yield f"data: {json.dumps(event)}\n\n"

        return Response(gen(), mimetype="text/event-stream")

    return app


def main(argv: list[str] | None = None) -> None:
    import argparse
    import logging
    import os

    from ..config import ExecutionConfig
    from ..ledger import Ledger
    from ..trade_once import _default_broker, _default_price
    from .run import default_chunk_source, run_events
    from .viewmodel import portfolio_view

    parser = argparse.ArgumentParser(prog="trading-dashboard")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    cfg = ExecutionConfig.from_env()
    broker = _default_broker(cfg)
    ledger = Ledger(
        os.path.join(os.path.expanduser("~"), ".tradingagents", "execution", "ledger.jsonl")
    )

    def run_fn(ticker, date):
        yield from run_events(
            ticker, date, cfg=cfg, broker=broker,
            chunk_source=default_chunk_source, price_fn=_default_price, ledger=ledger,
        )

    app = create_app(run_fn=run_fn, portfolio_fn=lambda: portfolio_view(broker, ledger))
    print(f"  trading-dashboard -> http://{args.host}:{args.port}")
    app.run(host=args.host, port=args.port, threaded=True)


if __name__ == "__main__":
    main()
