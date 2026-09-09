"""Entry strategies for volatile/moving stocks -- the missing half of the
stocks leg.

Why this module exists: the watchlist (state/watchlist.json) is filled from
Yahoo Finance's day-mover screeners (day_gainers, small_cap_gainers,
aggressive_small_caps, most_actives), but until now the only entry logic was
signals/darvas.py, which requires a TIGHT consolidation (box width <=12%
between the recent high and low). An audit of the 20 tickers on the live
watchlist measured an average box width of 23.97% -- roughly double the
Darvas ceiling -- and found 20/20 rejected, zero trades in the bot's
history. The screener hunts for what's already exploding; Darvas demands
quiet. They cancel each other out by construction.

The fix is not to loosen Darvas (that stops being Darvas) but to add
strategies whose entry condition matches what a mover screener actually
produces: a breakout without the width cap, sustained multi-day momentum,
a held opening gap, and a capitulation-volume reversal. Darvas stays in the
tournament as the "calm consolidation" specialist; these cover the rest of
the distribution.

Same contract as signals/strategies.py: pure functions, long-only, each
takes a yfinance-shaped OHLCV DataFrame (Open/High/Low/Close/Volume) and
returns a Proposal or None. None means "doesn't fire", not "broke" --
real failures (bad input, missing columns) raise instead of being
swallowed, so a silent bug never masquerades as "no signal".
"""
import pandas as pd

from signals.strategies import Proposal

_OHLCV_COLUMNS = ["Open", "High", "Low", "Close", "Volume"]


def range_breakout(
    hist: pd.DataFrame,
    window: int = 20,
    volume_multiplier: float = 1.5,
) -> Proposal | None:
    """Darvas's wide-consolidation cousin: same volume-confirmed breakout
    of a recent ceiling, minus the <=12% width requirement that filters out
    every actual mover. window=20 matches strategies.breakout's crypto
    breakout (comparable lookback across the tournament); volume_multiplier
    reuses Darvas's own 1.5x bar so a breakout isn't easier to trigger just
    because it skipped the width filter."""
    hist = hist.dropna(subset=_OHLCV_COLUMNS)
    if len(hist) < window + 1:
        return None

    window_period = hist.iloc[-(window + 1):-1]  # excludes today, same as darvas box_period
    prior_high = float(window_period["High"].max())
    today = hist.iloc[-1]
    today_close = float(today["Close"])
    if today_close <= prior_high:
        return None

    avg_volume = float(window_period["Volume"].mean())
    if avg_volume <= 0:
        return None
    volume_ratio = float(today["Volume"]) / avg_volume
    if volume_ratio < volume_multiplier:
        return None

    return Proposal(
        "range_breakout",
        f"ruptura del maximo de {window} sesiones, volumen {volume_ratio:.1f}x promedio",
        round(today_close, 2),
    )


def momentum_continuation(
    hist: pd.DataFrame,
    lookback: int = 10,
    min_total_return_pct: float = 15.0,
    max_single_day_share: float = 0.5,
) -> Proposal | None:
    """Buys a sustained climb, not a single green candle. The distinguishing
    mechanic: split the lookback's total price gain into its daily pieces
    and reject if any one day supplied more than max_single_day_share of
    it -- a real trend accumulates gains across several sessions, a "vela
    suelta" is one bar doing all the work with flat or red days around it.
    lookback=10 (two trading weeks) is long enough to tell the two apart but
    short enough to still catch a fast small-cap mid-move. min_total_return
    of 15% is set at roughly half the audited movers' average 23.97% box
    width, so this fires while the move is still likely underway rather
    than after it's fully priced. max_single_day_share=0.5 is the literal
    "no un solo dia de spike" test."""
    hist = hist.dropna(subset=_OHLCV_COLUMNS)
    if len(hist) < lookback + 1:
        return None

    closes = hist["Close"].astype(float).iloc[-(lookback + 1):].tolist()
    start, end = closes[0], closes[-1]
    if start <= 0:
        return None

    total_return_pct = (end - start) / start * 100
    if total_return_pct < min_total_return_pct:
        return None

    total_gain = end - start
    if total_gain <= 0:
        return None
    daily_gains = [closes[i + 1] - closes[i] for i in range(len(closes) - 1)]
    single_day_share = max(daily_gains) / total_gain
    if single_day_share > max_single_day_share:
        return None

    return Proposal(
        "momentum_continuation",
        f"+{total_return_pct:.1f}% en {lookback} sesiones, ningun dia aporto mas del "
        f"{max_single_day_share * 100:.0f}% del avance",
        round(end, 2),
    )


def gap_continuation(
    hist: pd.DataFrame,
    min_gap_pct: float = 4.0,
    min_hold_fraction: float = 0.5,
) -> Proposal | None:
    """Opened on a gap up and held it. min_gap_pct=4.0 is the rough floor
    where a gap stops being ordinary session noise and starts implying a
    catalyst (news/earnings) -- small caps routinely gap past this on a
    real trigger. min_hold_fraction=0.5 requires the close to retain at
    least half the gap versus the prior close, which is the difference
    between a held gap (real buying pressure) and a gap-and-crap (opened
    strong, got sold into the close)."""
    hist = hist.dropna(subset=_OHLCV_COLUMNS)
    if len(hist) < 2:
        return None

    prior_close = float(hist.iloc[-2]["Close"])
    if prior_close <= 0:
        return None
    today = hist.iloc[-1]
    today_open = float(today["Open"])
    today_close = float(today["Close"])

    gap_pct = (today_open - prior_close) / prior_close * 100
    if gap_pct < min_gap_pct:
        return None

    gap_size = today_open - prior_close  # > 0, guaranteed by the check above
    held = (today_close - prior_close) / gap_size
    if held < min_hold_fraction:
        return None

    # held can exceed 1.0 (close ran past the open) -- "sostuvo 143% del
    # gap" would read as nonsense, so word that case as an extension instead.
    if held >= 1.0:
        detail = "extendio el avance por encima de la apertura"
    else:
        detail = f"sostuvo {held * 100:.0f}% del gap al cierre"
    return Proposal(
        "gap_continuation",
        f"gap de apertura +{gap_pct:.1f}%, {detail}",
        round(today_close, 2),
    )


def capitulation_reversal(
    hist: pd.DataFrame,
    window: int = 10,
    min_decline_pct: float = 20.0,
    capitulation_volume_multiplier: float = 2.0,
    min_bounce_pct: float = 3.0,
) -> Proposal | None:
    """The contrarian entry: buy the confirmed bounce off a panic low, not
    the drop itself. window=10 mirrors momentum_continuation's lookback.
    min_decline_pct=20.0 sits near the audited movers' average 23.97% box
    width, so this only fires on panic-scale drops, not a routine pullback.
    capitulation_volume_multiplier=2.0 (vs. Darvas/range_breakout's 1.5x) is
    set higher on purpose -- a blow-off low should show materially more
    excess volume than an ordinary breakout day. min_bounce_pct=3.0 requires
    the reversal to already be visible before entry: this never buys the
    falling knife itself, only a confirmed move up off the low."""
    hist = hist.dropna(subset=_OHLCV_COLUMNS)
    if len(hist) < window + 2:
        return None

    recent = hist.iloc[-(window + 1):-1]  # window sessions, excludes today
    closes = recent["Close"].astype(float)
    peak = float(closes.max())
    trough = float(closes.min())
    if peak <= 0 or trough <= 0:
        return None
    # max()/min() alone don't require the low to come after the high -- a
    # rising window (low early, high late) would otherwise read as a
    # "decline" too. Real capitulation is peak-then-trough, in that order.
    if closes.values.argmin() <= closes.values.argmax():
        return None

    decline_pct = (peak - trough) / peak * 100
    if decline_pct < min_decline_pct:
        return None

    trough_idx = closes.idxmin()
    trough_volume = float(recent.loc[trough_idx, "Volume"])
    avg_volume = float(recent["Volume"].mean())
    if avg_volume <= 0:
        return None
    volume_ratio = trough_volume / avg_volume
    if volume_ratio < capitulation_volume_multiplier:
        return None

    today = hist.iloc[-1]
    today_close = float(today["Close"])
    prior_close = float(hist.iloc[-2]["Close"])
    if today_close <= prior_close:
        return None  # still falling -- no confirmed reversal yet

    bounce_pct = (today_close - trough) / trough * 100
    if bounce_pct < min_bounce_pct:
        return None

    return Proposal(
        "capitulation_reversal",
        f"caida de {decline_pct:.1f}% con volumen de panico ({volume_ratio:.1f}x promedio), "
        f"rebote confirmado de +{bounce_pct:.1f}% desde el piso",
        round(today_close, 2),
    )


# Registry, same shape as strategies.ALL_STRATEGIES -- adding a strategy
# here is what puts it in evaluate_all() below.
ALL_STOCK_STRATEGIES = {
    "range_breakout": range_breakout,
    "momentum_continuation": momentum_continuation,
    "gap_continuation": gap_continuation,
    "capitulation_reversal": capitulation_reversal,
}


def evaluate_all(hist: pd.DataFrame) -> list[Proposal]:
    """Every stock strategy's verdict on one symbol's history. A strategy
    that declines simply doesn't appear in the result -- see
    strategies.evaluate_all for why that's the desired behavior, not a
    gap. Deliberately does not catch exceptions: a strategy that raises has
    a real bug, and hiding that behind an empty result is exactly the
    masking this module is required not to do."""
    return [p for p in (fn(hist) for fn in ALL_STOCK_STRATEGIES.values()) if p is not None]
