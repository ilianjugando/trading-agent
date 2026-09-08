"""Manual smoke test: place ONE real (paper/demo) order directly through
the broker adapters, bypassing all strategy/signal detection, so you can
watch order placement, position tracking, and stop-loss exit fire
end-to-end without waiting for an organic market signal.

Refuses to run against anything but paper mode / OKX Demo Trading. A
bare invocation only prints what it would do -- pass --confirm to
actually place the order. Uses a dedicated "smoketest" pool for its
risk-guard/position state, so it goes through the same real SpendGuard/
CircuitBreaker gates as a live run, but can never touch or trip the
real stocks/crypto pools' budgets.

    python -m scripts.smoke_test_order --broker okx --symbol BTC-USDT --confirm
    python -m scripts.smoke_test_order --broker ibkr --symbol NVDA --confirm
    python -m scripts.smoke_test_order --broker okx --symbol BTC-USDT --close --confirm
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from brokers.ibkr_adapter import IBKRAdapter
from brokers.okx_adapter import OKXAdapter
from config.settings import load_settings
from execution.orchestrator import _log
from execution.positions import PositionTracker
from risk.circuit_breaker import CircuitBreaker
from risk.spend_guard import SpendGuard

POOL = "smoketest"
USD_AMOUNT = 15.0  # small, fixed -- clears per-trade caps comfortably


def refuse_unless_safe(settings, broker: str, confirm: bool) -> None:
    if settings.mode != "paper":
        sys.exit(f"Refusing: TRADING_MODE is {settings.mode!r}. This script only runs with --mode paper.")
    if broker == "okx" and settings.okx_demo_flag != "1":
        sys.exit("Refusing: OKX_DEMO_FLAG is not '1' even though mode is paper -- check .env.")
    if not confirm:
        port_note = f"IBKR port {settings.ibkr_port}" if broker == "ibkr" else "OKX Demo Trading"
        print(f"Dry run (no --confirm passed). Would place a real order against {port_note}.")
        print("Re-run with --confirm to actually do it.")
        sys.exit(0)


def place_ibkr(settings, symbol: str) -> dict:
    ibkr = IBKRAdapter(settings.ibkr_host, settings.ibkr_port, settings.ibkr_client_id)
    try:
        result = ibkr.place_market_order_by_qty(symbol, qty=1, action="BUY")
        return result
    finally:
        ibkr.disconnect()


def place_okx(settings, symbol: str) -> dict:
    okx = OKXAdapter(settings.okx_api_key, settings.okx_api_secret, settings.okx_api_passphrase, settings.okx_demo_flag)
    result = okx.place_market_order(symbol, USD_AMOUNT, "buy")
    # place_market_order's response has no fill qty/price (a buy is sized
    # in quote currency) -- fetch both so the position can be closed later
    # with the correct base-currency amount.
    result["qty"] = okx.get_filled_base_qty(symbol, result["ordId"])
    result["price"] = okx.get_last_price(symbol)
    return result


def close_position(settings, broker: str, symbol: str, positions: PositionTracker) -> None:
    pos = positions.get(symbol)
    if not pos:
        sys.exit(f"No open smoketest position for {symbol} to close.")

    if broker == "ibkr":
        ibkr = IBKRAdapter(settings.ibkr_host, settings.ibkr_port, settings.ibkr_client_id)
        try:
            result = ibkr.place_market_order_by_qty(symbol, qty=pos["qty"], action="SELL")
        finally:
            ibkr.disconnect()
    else:
        okx = OKXAdapter(settings.okx_api_key, settings.okx_api_secret, settings.okx_api_passphrase, settings.okx_demo_flag)
        result = okx.place_market_order(symbol, pos["qty"], "sell")

    positions.close(symbol)
    _log(settings.logs_dir, "trades.log", {"pool": POOL, "action": "close", **result})
    _log(settings.logs_dir, "decisions.log", {"pool": POOL, "symbol": symbol, "result": "smoketest_closed"})
    print(f"Closed smoketest position: {result}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--broker", choices=["ibkr", "okx"], required=True)
    parser.add_argument("--symbol", required=True, help="e.g. NVDA for ibkr, BTC-USDT for okx")
    parser.add_argument("--confirm", action="store_true", help="actually place the order (default: dry run)")
    parser.add_argument("--close", action="store_true", help="close an existing smoketest position instead of opening one")
    args = parser.parse_args()

    import os

    os.environ["TRADING_MODE"] = "paper"
    settings = load_settings()
    refuse_unless_safe(settings, args.broker, args.confirm)

    guard = SpendGuard(POOL, settings.state_dir, settings.max_trade_pct, settings.daily_loss_halt_pct)
    breaker = CircuitBreaker(POOL, settings.state_dir, settings.daily_loss_halt_pct, settings.max_consecutive_losses)
    positions = PositionTracker(POOL, settings.state_dir)

    if args.close:
        close_position(settings, args.broker, args.symbol, positions)
        return

    breaker.check(pool_value=1_000_000)  # smoketest pool has no real "value" to track drawdown against
    guard.check_and_record(USD_AMOUNT, pool_value=1_000_000)

    result = place_ibkr(settings, args.symbol) if args.broker == "ibkr" else place_okx(settings, args.symbol)
    # A placeholder stop only so PositionTracker (which requires one) has
    # something to store -- this script doesn't implement real stop-loss
    # logic, it's a connectivity/lifecycle smoke test, not a strategy.
    stop = result["price"] * 0.9

    positions.open(args.symbol, entry_price=result["price"], qty=result["qty"], stop=stop)
    _log(settings.logs_dir, "trades.log", {"pool": POOL, **result})
    _log(settings.logs_dir, "decisions.log", {"pool": POOL, "symbol": args.symbol, "result": "smoketest_opened"})

    print(f"Order placed: {result}")
    print(f"Position recorded in state/positions_{POOL}.json")
    print(f"Check dashboard.py or logs/trades.log (pool={POOL!r}) to see it.")
    print(f"Run with --close --confirm to exercise the exit path.")


if __name__ == "__main__":
    main()
