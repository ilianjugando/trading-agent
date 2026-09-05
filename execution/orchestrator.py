"""Main entrypoint. One run = one pass over stocks and/or crypto:
compute signal -> Gemini review -> risk checks -> place order -> log.
Intended to be invoked on a schedule (Windows Task Scheduler) rather than
run as a long-lived process. See README.md for setup.
"""
import argparse
import json
import sys
import traceback
from dataclasses import asdict
from datetime import datetime, timezone

from brokers.ibkr_adapter import IBKRAdapter
from brokers.okx_adapter import OKXAdapter
from config.settings import load_settings
from execution.positions import PositionTracker
from risk.circuit_breaker import CircuitBreaker, TradingHalted
from risk.spend_guard import SpendGuard, SpendLimitError
from signals.crypto_trend import rank_universe as rank_crypto
from signals.darvas import compute_box, scan_for_breakouts
from signals.llm_review import review_signal


def _log(logs_dir, filename: str, record: dict) -> None:
    logs_dir.mkdir(parents=True, exist_ok=True)
    record = {"timestamp": datetime.now(timezone.utc).isoformat(), **record}
    with open(logs_dir / filename, "a") as f:
        f.write(json.dumps(record) + "\n")


def run_stocks(settings) -> None:
    guard = SpendGuard("stocks", settings.state_dir, settings.max_trade_pct, settings.daily_loss_halt_pct)
    breaker = CircuitBreaker("stocks", settings.state_dir, settings.daily_loss_halt_pct, settings.max_consecutive_losses)
    positions = PositionTracker("stocks", settings.state_dir)

    ibkr = IBKRAdapter(settings.ibkr_host, settings.ibkr_port, settings.ibkr_client_id)
    try:
        pool_value = ibkr.get_account_value()
        breaker.check(pool_value)

        # Manage existing positions first: exit on a stop-loss hit, otherwise
        # trail the stop up if a fresh, higher box has formed.
        for symbol, pos in list(positions.all_open().items()):
            price = ibkr.get_last_price(symbol)
            if price <= pos["stop"]:
                result = ibkr.place_market_order_by_qty(symbol, pos["qty"], "SELL")
                won = price > pos["entry_price"]
                positions.close(symbol)
                breaker.record_trade_result(won)
                _log(settings.logs_dir, "trades.log", {"pool": "stocks", "reason": "stop_loss", **result})
                _log(settings.logs_dir, "decisions.log", {
                    "pool": "stocks", "symbol": symbol, "result": "stopped_out", "won": won, "stop": pos["stop"],
                })
                continue

            box = compute_box(symbol)
            if box and box.box_bottom > pos["stop"]:
                positions.update_stop(symbol, box.box_bottom)
                _log(settings.logs_dir, "decisions.log", {
                    "pool": "stocks", "symbol": symbol, "result": "stop_trailed", "new_stop": box.box_bottom,
                })

        # Look for a new breakout among symbols not already held.
        held = set(positions.all_open().keys())
        candidates = [b for b in scan_for_breakouts() if b.symbol not in held]
        if not candidates:
            _log(settings.logs_dir, "decisions.log", {"pool": "stocks", "result": "no_signals"})
            return

        top = candidates[0]
        review = review_signal(settings.gemini_api_key, asdict(top))
        decision_record = {"pool": "stocks", "signal": asdict(top), "review": asdict(review)}

        if review.action != "buy" or review.confidence < 0.6:
            _log(settings.logs_dir, "decisions.log", {**decision_record, "result": "skipped"})
            return

        trade_usd = pool_value * settings.max_trade_pct
        guard.check_and_record(trade_usd, pool_value)

        result = ibkr.place_market_order(top.symbol, trade_usd, "BUY")
        positions.open(top.symbol, entry_price=result["price"], qty=result["qty"], stop=top.box_bottom)
        _log(settings.logs_dir, "decisions.log", {**decision_record, "result": "executed"})
        _log(settings.logs_dir, "trades.log", {"pool": "stocks", **result})

    except TradingHalted as e:
        _log(settings.logs_dir, "decisions.log", {"pool": "stocks", "result": "halted", "reason": str(e)})
    except SpendLimitError as e:
        _log(settings.logs_dir, "decisions.log", {"pool": "stocks", "result": "rejected_by_spend_guard", "reason": str(e)})
    finally:
        ibkr.disconnect()


def run_crypto(settings) -> None:
    guard = SpendGuard("crypto", settings.state_dir, settings.max_trade_pct, settings.daily_loss_halt_pct)
    breaker = CircuitBreaker("crypto", settings.state_dir, settings.daily_loss_halt_pct, settings.max_consecutive_losses)

    okx = OKXAdapter(settings.okx_api_key, settings.okx_api_secret, settings.okx_api_passphrase, settings.okx_demo_flag)

    pool_value = okx.get_usdt_balance()
    breaker.check(pool_value)

    ranked = rank_crypto(okx)
    if not ranked:
        _log(settings.logs_dir, "decisions.log", {"pool": "crypto", "result": "no_signals"})
        return

    top = ranked[0]
    review = review_signal(settings.gemini_api_key, asdict(top))
    decision_record = {"pool": "crypto", "signal": asdict(top), "review": asdict(review)}

    try:
        if review.action != "buy" or review.confidence < 0.6:
            _log(settings.logs_dir, "decisions.log", {**decision_record, "result": "skipped"})
            return

        trade_usd = pool_value * settings.max_trade_pct
        guard.check_and_record(trade_usd, pool_value)

        result = okx.place_market_order(top.inst_id, trade_usd, "buy")
        _log(settings.logs_dir, "decisions.log", {**decision_record, "result": "executed"})
        _log(settings.logs_dir, "trades.log", {"pool": "crypto", **result})

    except TradingHalted as e:
        _log(settings.logs_dir, "decisions.log", {"pool": "crypto", "result": "halted", "reason": str(e)})
    except SpendLimitError as e:
        _log(settings.logs_dir, "decisions.log", {"pool": "crypto", "result": "rejected_by_spend_guard", "reason": str(e)})


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["paper", "live"], required=True)
    parser.add_argument("--pool", choices=["stocks", "crypto", "both"], default="both")
    args = parser.parse_args()

    import os
    os.environ["TRADING_MODE"] = args.mode
    settings = load_settings()

    try:
        if args.pool in ("stocks", "both"):
            run_stocks(settings)
        if args.pool in ("crypto", "both"):
            run_crypto(settings)
    except Exception:
        _log(settings.logs_dir, "decisions.log", {"pool": args.pool, "result": "error", "traceback": traceback.format_exc()})
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
