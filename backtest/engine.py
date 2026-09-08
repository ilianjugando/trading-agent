"""Backtest engine: replays the *existing* deterministic signal functions
(compute_box, rank_universe) over historical data. No LLM calls, no live
orders -- results are an UPPER BOUND on trade frequency/returns, since
the live LLM panel can only reduce how many of these signals actually
execute, never add to them.
"""
from dataclasses import dataclass

import pandas as pd

from signals.crypto_trend import rank_universe
from signals.darvas import compute_box


@dataclass
class BacktestTrade:
    symbol: str
    entry_date: str
    entry_price: float
    exit_date: str | None = None
    exit_price: float | None = None
    exit_reason: str | None = None  # "stop_loss" | "end_of_window"
    pnl_pct: float | None = None


@dataclass
class BacktestResult:
    pool: str
    window_start: str
    window_end: str
    trades: list[BacktestTrade]
    win_rate: float | None
    total_return_pct: float
    max_drawdown_pct: float


def walk_forward_stocks(
    symbol: str,
    history: pd.DataFrame,
    box_window: int = 10,
    near_high_tolerance: float = 0.05,
    max_box_width_pct: float = 0.12,
    volume_multiplier: float = 1.5,
) -> list[BacktestTrade]:
    """Slides a day-by-day window over `history` (a yfinance-shaped OHLCV
    DataFrame), calling compute_box() exactly as run_stocks() does: open
    on a confirmed breakout, trail the stop up whenever a freshly
    computed box has a higher bottom, close when a day's low breaches
    the stop."""
    trades: list[BacktestTrade] = []
    open_trade: BacktestTrade | None = None
    stop: float | None = None
    min_rows = box_window + 6

    for i in range(min_rows, len(history)):
        window = history.iloc[: i + 1]
        today = window.iloc[-1]
        box = compute_box(
            symbol,
            box_window=box_window,
            near_high_tolerance=near_high_tolerance,
            max_box_width_pct=max_box_width_pct,
            volume_multiplier=volume_multiplier,
            history=window,
        )

        if open_trade is None:
            if box and box.breakout:
                open_trade = BacktestTrade(
                    symbol=symbol, entry_date=str(window.index[-1].date()), entry_price=box.last_close
                )
                stop = box.box_bottom
            continue

        if float(today["Low"]) <= stop:
            open_trade.exit_date = str(window.index[-1].date())
            open_trade.exit_price = stop
            open_trade.exit_reason = "stop_loss"
            open_trade.pnl_pct = round((stop - open_trade.entry_price) / open_trade.entry_price * 100, 2)
            trades.append(open_trade)
            open_trade = None
            stop = None
        elif box and box.box_bottom > stop:
            stop = box.box_bottom

    if open_trade is not None:
        last_close = float(history.iloc[-1]["Close"])
        open_trade.exit_date = str(history.index[-1].date())
        open_trade.exit_price = last_close
        open_trade.exit_reason = "end_of_window"
        open_trade.pnl_pct = round((last_close - open_trade.entry_price) / open_trade.entry_price * 100, 2)
        trades.append(open_trade)

    return trades


def summarize(pool: str, window_start: str, window_end: str, trades: list[BacktestTrade]) -> BacktestResult:
    closed = [t for t in trades if t.pnl_pct is not None]
    win_rate = round(sum(1 for t in closed if t.pnl_pct > 0) / len(closed) * 100, 1) if closed else None

    equity = 100.0
    peak = equity
    max_dd = 0.0
    for t in closed:
        equity *= 1 + t.pnl_pct / 100
        peak = max(peak, equity)
        max_dd = max(max_dd, (peak - equity) / peak * 100)

    return BacktestResult(
        pool=pool,
        window_start=window_start,
        window_end=window_end,
        trades=trades,
        win_rate=win_rate,
        total_return_pct=round(equity - 100.0, 2),
        max_drawdown_pct=round(max_dd, 2),
    )


class _ReplayOKX:
    """Duck-typed stand-in for OKXAdapter at one point in historical
    time, so rank_universe() can be replayed with no live connection."""

    def __init__(self, pct_by_inst: dict[str, float]):
        self._pct_by_inst = pct_by_inst

    def get_24h_change_pct(self, inst_id: str) -> float:
        return self._pct_by_inst[inst_id]


@dataclass
class CryptoSignalEvent:
    hours_ago: int
    inst_id: str
    change_24h_pct: float


def walk_forward_crypto(closes_by_inst: dict[str, list[float]], bars_per_day_lookback: int = 24) -> list[CryptoSignalEvent]:
    """The live system (signals/crypto_trend.py + orchestrator.run_crypto)
    has no coded exit/position lifecycle for crypto -- it only ever picks
    one top momentum candidate and places a single order. So this replays
    only that: how often, and how strongly, a top candidate would have
    appeared. It is a signal-frequency count, not a P&L backtest -- there
    is no exit rule in the live system to backtest."""
    n = min(len(c) for c in closes_by_inst.values())
    events: list[CryptoSignalEvent] = []

    for i in range(bars_per_day_lookback, n):
        pct_by_inst = {}
        for inst_id, closes in closes_by_inst.items():
            past, now = closes[i - bars_per_day_lookback], closes[i]
            if past:
                pct_by_inst[inst_id] = (now - past) / past

        ranked = rank_universe(_ReplayOKX(pct_by_inst), universe=list(pct_by_inst))
        if ranked:
            top = ranked[0]
            events.append(CryptoSignalEvent(hours_ago=n - 1 - i, inst_id=top.inst_id, change_24h_pct=top.change_24h_pct))

    return events
