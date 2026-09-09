"""Darvas box breakout signal.

A box is a tight recent consolidation range near the stock's highs. A buy
signal fires when price closes above the box top on above-average volume
(confirms real demand, not noise). The box bottom doubles as the
stop-loss level -- that's the whole point of the theory: never guess a
stop, read it off the chart.

This is a mechanical adaptation for daily bars -- the original Darvas
method used real-time ticker tape and judgment calls on where a box
starts/ends. The parameters below (box_window, max_box_width_pct,
near_high_tolerance, volume_multiplier) are the algorithmic stand-in for
that judgment.
"""
from dataclasses import dataclass

import pandas as pd
import yfinance as yf

from config.universe import resolve_stock_universe


@dataclass
class DarvasBox:
    symbol: str
    box_top: float
    box_bottom: float
    box_width_pct: float
    last_close: float
    volume_ratio: float
    breakout: bool


def compute_box(
    symbol: str,
    box_window: int = 10,
    near_high_tolerance: float = 0.05,
    max_box_width_pct: float = 0.12,
    volume_multiplier: float = 1.5,
    history: pd.DataFrame | None = None,
) -> DarvasBox | None:
    """Pass `history` (a yfinance-shaped OHLCV DataFrame) to avoid a network
    call, e.g. when re-checking a symbol you already fetched, or in tests."""
    hist = history if history is not None else yf.Ticker(symbol).history(period="3mo")
    hist = hist.dropna(subset=["Close", "High", "Low", "Volume"])
    if len(hist) < box_window + 5:
        return None

    box_period = hist.iloc[-(box_window + 1):-1]  # the consolidation window, excluding today
    box_top = float(box_period["High"].max())
    box_bottom = float(box_period["Low"].min())
    if box_bottom <= 0:
        return None

    box_width_pct = (box_top - box_bottom) / box_bottom
    # Excludes today: today is the breakout attempt, and its own high would
    # otherwise inflate this yardstick, unfairly penalizing the strongest
    # breakouts (the ones that clear the box top by the most).
    period_high = float(hist.iloc[:-1]["High"].max())
    near_high = box_top >= period_high * (1 - near_high_tolerance)
    is_valid_box = box_width_pct <= max_box_width_pct and near_high
    if not is_valid_box:
        return None

    today = hist.iloc[-1]
    avg_box_volume = float(box_period["Volume"].mean())
    volume_ratio = float(today["Volume"]) / avg_box_volume if avg_box_volume > 0 else 0.0
    breakout = bool(today["Close"] > box_top and volume_ratio >= volume_multiplier)

    return DarvasBox(
        symbol=symbol,
        box_top=round(box_top, 2),
        box_bottom=round(box_bottom, 2),
        box_width_pct=round(box_width_pct * 100, 2),
        last_close=round(float(today["Close"]), 2),
        volume_ratio=round(volume_ratio, 2),
        breakout=breakout,
    )


def scan_for_breakouts(basket: list[str] | None = None) -> list[DarvasBox]:
    """Only symbols with a confirmed breakout today, strongest volume first.
    basket defaults to resolve_stock_universe() (the state/watchlist.json
    override, falling back to ROBOTICS_BASKET) resolved fresh on every call
    -- not a function-default value baked in at import time, since the
    override file can change between scheduled runs."""
    if basket is None:
        basket = resolve_stock_universe()
    boxes = []
    for symbol in basket:
        try:
            box = compute_box(symbol)
            if box and box.breakout:
                boxes.append(box)
        except Exception:
            continue
    return sorted(boxes, key=lambda b: b.volume_ratio, reverse=True)
