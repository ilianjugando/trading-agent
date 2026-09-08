"""Standalone CLI: replay Darvas breakout signals over historical data.
Does NOT call the LLM panel or place any order -- results are an UPPER
BOUND on trade frequency, since the live panel can only reduce how many
of these signals actually execute.

    python -m backtest.run_stocks_backtest --period 2y
"""
import argparse
import json
import sys
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import yfinance as yf

from backtest.engine import summarize, walk_forward_stocks
from config.universe import ROBOTICS_BASKET

OUT_DIR = Path(__file__).resolve().parent.parent / "backtests"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--period", default="2y", help="yfinance period, e.g. 1y/2y/5y")
    parser.add_argument("--symbols", nargs="*", default=ROBOTICS_BASKET)
    args = parser.parse_args()

    OUT_DIR.mkdir(exist_ok=True)
    results = []

    for symbol in args.symbols:
        hist = yf.Ticker(symbol).history(period=args.period)
        if hist.empty:
            print(f"{symbol}: no data, skipping")
            continue
        trades = walk_forward_stocks(symbol, hist)
        result = summarize("stocks", str(hist.index[0].date()), str(hist.index[-1].date()), trades)
        results.append(result)
        print(
            f"{symbol}: {len(trades)} trade(s), win rate {result.win_rate}%, "
            f"total return {result.total_return_pct}%, max drawdown {result.max_drawdown_pct}%"
        )

    out_path = OUT_DIR / f"stocks_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')}.json"
    out_path.write_text(json.dumps([asdict(r) for r in results], indent=2))
    print(f"\nSaved: {out_path}")
    print(
        "\nNote: no LLM panel review is applied here -- these are an UPPER BOUND on\n"
        "how often the live system would actually trade, since the panel can only\n"
        "reduce how many of these signals get approved, never add to them."
    )


if __name__ == "__main__":
    main()
