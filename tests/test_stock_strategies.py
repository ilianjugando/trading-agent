import dataclasses
import json

import pandas as pd

from signals.stock_strategies import (
    ALL_STOCK_STRATEGIES,
    capitulation_reversal,
    evaluate_all,
    gap_continuation,
    momentum_continuation,
    range_breakout,
)


def _df(open_, high, low, close, volume):
    assert len(open_) == len(high) == len(low) == len(close) == len(volume)
    return pd.DataFrame({"Open": open_, "High": high, "Low": low, "Close": close, "Volume": volume})


# ---------------------------------------------------------------------------
# range_breakout
# ---------------------------------------------------------------------------

def _range_breakout_history(today_close, today_high, today_low, today_volume):
    """20 flat sessions (ceiling at High=100) then today's attempt."""
    n = 20
    highs = [100] * n + [today_high]
    lows = [95] * n + [today_low]
    closes = [98] * n + [today_close]
    opens = closes
    volumes = [1_000_000] * n + [today_volume]
    return _df(opens, highs, lows, closes, volumes)


def test_range_breakout_fires_on_volume_confirmed_break():
    hist = _range_breakout_history(today_close=110, today_high=111, today_low=105, today_volume=2_000_000)
    proposal = range_breakout(hist)
    assert proposal is not None
    assert proposal.strategy == "range_breakout"
    assert proposal.entry_price == 110.0
    # numpy.float64 subclasses float, so isinstance(x, float) alone would
    # not have caught the numpy-scalar bug the user flagged -- json.dumps
    # is the actual pipeline-shaped check (it raises TypeError on np.float64).
    json.dumps(dataclasses.asdict(proposal))


def test_range_breakout_none_when_price_stays_under_ceiling():
    hist = _range_breakout_history(today_close=99, today_high=100, today_low=97, today_volume=2_000_000)
    assert range_breakout(hist) is None


def test_range_breakout_none_without_volume_confirmation():
    hist = _range_breakout_history(today_close=110, today_high=111, today_low=105, today_volume=1_100_000)
    assert range_breakout(hist) is None


def test_range_breakout_none_on_insufficient_history():
    hist = _df([98] * 5, [100] * 5, [95] * 5, [98] * 5, [1_000_000] * 5)
    assert range_breakout(hist) is None


# ---------------------------------------------------------------------------
# momentum_continuation
# ---------------------------------------------------------------------------

def _momentum_history(closes):
    n = len(closes)
    highs = [c + 1 for c in closes]
    lows = [c - 1 for c in closes]
    opens = closes
    volumes = [1_000_000] * n
    return _df(opens, highs, lows, closes, volumes)


def test_momentum_continuation_fires_on_sustained_climb():
    # +2 every session for 10 sessions -> +20% total, evenly distributed
    closes = [100 + 2 * i for i in range(11)]
    hist = _momentum_history(closes)
    proposal = momentum_continuation(hist)
    assert proposal is not None
    assert proposal.strategy == "momentum_continuation"
    assert proposal.entry_price == 120.0
    json.dumps(dataclasses.asdict(proposal))


def test_momentum_continuation_none_on_single_day_spike():
    # flat for 9 sessions then one +20 jump -> same +20% total, but one bar did it all
    closes = [100] * 10 + [120]
    hist = _momentum_history(closes)
    assert momentum_continuation(hist) is None


def test_momentum_continuation_none_on_weak_total_return():
    # +0.5 every session for 10 sessions -> only +5% total, below the 15% floor
    closes = [100 + 0.5 * i for i in range(11)]
    hist = _momentum_history(closes)
    assert momentum_continuation(hist) is None


def test_momentum_continuation_none_on_insufficient_history():
    hist = _momentum_history([100, 101, 102])
    assert momentum_continuation(hist) is None


# ---------------------------------------------------------------------------
# gap_continuation
# ---------------------------------------------------------------------------

def _gap_history(prior_close, today_open, today_close):
    opens = [prior_close, today_open]
    closes = [prior_close, today_close]
    highs = [max(o, c) + 1 for o, c in zip(opens, closes)]
    lows = [min(o, c) - 1 for o, c in zip(opens, closes)]
    volumes = [1_000_000, 1_000_000]
    return _df(opens, highs, lows, closes, volumes)


def test_gap_continuation_fires_on_held_gap():
    # +5% gap, closes at 104 (holds 80% of the gap)
    hist = _gap_history(prior_close=100, today_open=105, today_close=104)
    proposal = gap_continuation(hist)
    assert proposal is not None
    assert proposal.strategy == "gap_continuation"
    assert proposal.entry_price == 104.0
    json.dumps(dataclasses.asdict(proposal))


def test_gap_continuation_none_on_small_gap():
    # +2% gap -- below the 4% floor
    hist = _gap_history(prior_close=100, today_open=102, today_close=102)
    assert gap_continuation(hist) is None


def test_gap_continuation_none_on_faded_gap():
    # +6% gap but closes back near prior_close -- only holds ~17% of the gap
    hist = _gap_history(prior_close=100, today_open=106, today_close=101)
    assert gap_continuation(hist) is None


def test_gap_continuation_none_on_insufficient_history():
    hist = _gap_history(prior_close=100, today_open=105, today_close=104).iloc[-1:]
    assert gap_continuation(hist) is None


# ---------------------------------------------------------------------------
# capitulation_reversal
# ---------------------------------------------------------------------------

def _capitulation_history(recent_closes, recent_volumes, today_close):
    """1 warm-up row + 10 "recent" rows (decline into the trough) + today."""
    warmup_close = [105.0]
    warmup_volume = [1_000_000]
    closes = warmup_close + recent_closes + [today_close]
    volumes = warmup_volume + recent_volumes + [1_000_000]
    opens = closes
    highs = [c + 1 for c in closes]
    lows = [c - 1 for c in closes]
    return _df(opens, highs, lows, closes, volumes)


_DECLINE_40PCT = [100, 95, 90, 85, 80, 75, 70, 65, 62, 60]  # peak 100 -> trough 60


def test_capitulation_reversal_fires_on_confirmed_bounce():
    volumes = [1_000_000] * 9 + [3_000_000]  # capitulation spike on the trough day
    hist = _capitulation_history(_DECLINE_40PCT, volumes, today_close=65)
    proposal = capitulation_reversal(hist)
    assert proposal is not None
    assert proposal.strategy == "capitulation_reversal"
    assert proposal.entry_price == 65.0
    json.dumps(dataclasses.asdict(proposal))


def test_capitulation_reversal_none_on_shallow_decline():
    shallow = [100, 98, 96, 94, 92, 90, 89, 88, 86, 85]  # only -15%
    volumes = [1_000_000] * 9 + [3_000_000]
    hist = _capitulation_history(shallow, volumes, today_close=90)
    assert capitulation_reversal(hist) is None


def test_capitulation_reversal_none_without_capitulation_volume():
    volumes = [1_000_000] * 10  # trough day has ordinary volume, no panic spike
    hist = _capitulation_history(_DECLINE_40PCT, volumes, today_close=65)
    assert capitulation_reversal(hist) is None


def test_capitulation_reversal_none_without_bounce_confirmation():
    volumes = [1_000_000] * 9 + [3_000_000]
    hist = _capitulation_history(_DECLINE_40PCT, volumes, today_close=59)  # still falling
    assert capitulation_reversal(hist) is None


def test_capitulation_reversal_none_on_insufficient_history():
    volumes = [1_000_000] * 9 + [3_000_000]
    hist = _capitulation_history(_DECLINE_40PCT, volumes, today_close=65).iloc[-8:]
    assert capitulation_reversal(hist) is None


def test_capitulation_reversal_none_on_rising_window():
    # low comes FIRST, high comes LAST -- a rally, not a decline-into-trough.
    # max()/min() alone can't tell these apart; this is what catches that.
    rising = [60, 65, 70, 75, 80, 85, 90, 92, 95, 100]
    volumes = [3_000_000] + [1_000_000] * 9  # "panic" volume on the early low
    hist = _capitulation_history(rising, volumes, today_close=105)
    assert capitulation_reversal(hist) is None


# ---------------------------------------------------------------------------
# evaluate_all / registry
# ---------------------------------------------------------------------------

def test_evaluate_all_collects_firing_strategies():
    hist = _range_breakout_history(today_close=110, today_high=111, today_low=105, today_volume=2_000_000)
    proposals = evaluate_all(hist)
    assert any(p.strategy == "range_breakout" for p in proposals)
    assert all(p.__class__.__name__ == "Proposal" for p in proposals)


def test_evaluate_all_empty_on_flat_history():
    hist = _df([100] * 25, [101] * 25, [99] * 25, [100] * 25, [1_000_000] * 25)
    assert evaluate_all(hist) == []


def test_registry_contains_all_four_strategies():
    assert set(ALL_STOCK_STRATEGIES) == {
        "range_breakout",
        "momentum_continuation",
        "gap_continuation",
        "capitulation_reversal",
    }
