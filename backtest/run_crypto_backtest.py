"""Standalone CLI: replay crypto momentum ranking over historical OKX
candles. Does NOT call the LLM panel or place any order.

Note: the live crypto pipeline (signals/crypto_trend.py +
orchestrator.run_crypto) has no coded exit/position rule -- it only
ever picks one top candidate and places a single order. So this reports
how often, and how strongly, a top candidate would have appeared --
not a full profit/loss backtest, since there's no exit rule in the live
system to backtest.

    python -m backtest.run_crypto_backtest --days 90
"""
import argparse
import json
import os
import sys
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backtest.engine import walk_forward_crypto
from brokers.okx_adapter import OKXAdapter
from config.settings import load_settings
from config.universe import CRYPTO_UNIVERSE

OUT_DIR = Path(__file__).resolve().parent.parent / "backtests"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=90)
    parser.add_argument("--symbols", nargs="*", default=CRYPTO_UNIVERSE)
    args = parser.parse_args()

    os.environ.setdefault("TRADING_MODE", "paper")
    settings = load_settings()
    okx = OKXAdapter(settings.okx_api_key, settings.okx_api_secret, settings.okx_api_passphrase, settings.okx_demo_flag)

    OUT_DIR.mkdir(exist_ok=True)
    closes_by_inst = {}
    for inst_id in args.symbols:
        closes = okx.get_history_candles(inst_id, bar="1H", days=args.days)
        if closes:
            closes_by_inst[inst_id] = closes
        else:
            print(f"{inst_id}: no data, skipping")

    if not closes_by_inst:
        print("No data fetched for any symbol.")
        return

    events = walk_forward_crypto(closes_by_inst)

    print(f"{len(events)} top-candidate signal event(s) over {args.days} days across {len(closes_by_inst)} symbol(s)")
    for inst_id in closes_by_inst:
        count = sum(1 for e in events if e.inst_id == inst_id)
        print(f"  {inst_id}: picked as top candidate {count} time(s)")

    out_path = OUT_DIR / f"crypto_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')}.json"
    out_path.write_text(json.dumps([asdict(e) for e in events], indent=2))
    print(f"\nSaved: {out_path}")
    print(
        "\nNote: the live crypto pipeline has no coded exit/position rule -- this\n"
        "counts how often a signal would appear, not a profit/loss backtest. No LLM\n"
        "panel review is applied either -- an upper bound on how often the live\n"
        "system would actually place an order."
    )


if __name__ == "__main__":
    main()
