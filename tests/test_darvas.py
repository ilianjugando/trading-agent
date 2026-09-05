import pandas as pd

from signals.darvas import compute_box


def _make_history(box_highs, box_lows, box_volumes, today_close, today_high, today_low, today_volume):
    """5 warm-up days rising into the box, then the box period, then today."""
    warmup_high = [90, 92, 94, 96, 98]
    warmup_low = [86, 88, 90, 92, 94]
    warmup_close = [88, 90, 92, 94, 96]
    warmup_volume = [1_000_000] * 5

    highs = warmup_high + box_highs + [today_high]
    lows = warmup_low + box_lows + [today_low]
    closes = warmup_close + [(h + l) / 2 for h, l in zip(box_highs, box_lows)] + [today_close]
    volumes = warmup_volume + box_volumes + [today_volume]

    return pd.DataFrame({
        "Open": closes,
        "High": highs,
        "Low": lows,
        "Close": closes,
        "Volume": volumes,
    })


def test_confirmed_breakout():
    hist = _make_history(
        box_highs=[100, 101, 102, 101, 100, 101, 102, 101, 100, 102],
        box_lows=[97, 98, 97, 98, 97, 98, 97, 98, 97, 98],
        box_volumes=[1_000_000] * 10,
        today_close=106,
        today_high=107,
        today_low=102,
        today_volume=2_000_000,  # 2x average box volume
    )
    box = compute_box("TEST", history=hist)
    assert box is not None
    assert box.box_top == 102
    assert box.box_bottom == 97
    assert box.breakout is True


def test_breakout_without_volume_confirmation_is_rejected():
    hist = _make_history(
        box_highs=[100, 101, 102, 101, 100, 101, 102, 101, 100, 102],
        box_lows=[97, 98, 97, 98, 97, 98, 97, 98, 97, 98],
        box_volumes=[1_000_000] * 10,
        today_close=106,
        today_high=107,
        today_low=102,
        today_volume=1_100_000,  # only 1.1x average -- below the 1.5x threshold
    )
    box = compute_box("TEST", history=hist)
    assert box is not None
    assert box.breakout is False


def test_no_breakout_when_price_stays_in_box():
    hist = _make_history(
        box_highs=[100, 101, 102, 101, 100, 101, 102, 101, 100, 102],
        box_lows=[97, 98, 97, 98, 97, 98, 97, 98, 97, 98],
        box_volumes=[1_000_000] * 10,
        today_close=100,  # inside the box, not above box_top
        today_high=101,
        today_low=98,
        today_volume=2_000_000,
    )
    box = compute_box("TEST", history=hist)
    assert box is not None
    assert box.breakout is False


def test_wide_range_is_not_a_valid_box():
    # 30%+ range -- too volatile to call a consolidation "box"
    hist = _make_history(
        box_highs=[130, 110, 130, 100, 130, 110, 130, 100, 130, 100],
        box_lows=[100, 100, 100, 100, 100, 100, 100, 100, 100, 100],
        box_volumes=[1_000_000] * 10,
        today_close=135,
        today_high=136,
        today_low=131,
        today_volume=2_000_000,
    )
    box = compute_box("TEST", history=hist)
    assert box is None


def test_strong_breakout_does_not_fail_near_high_check():
    """A large breakout shouldn't be penalized by comparing the box top
    against a 'period high' that includes today's own new high."""
    hist = _make_history(
        box_highs=[100, 101, 102, 101, 100, 101, 102, 101, 100, 102],
        box_lows=[97, 98, 97, 98, 97, 98, 97, 98, 97, 98],
        box_volumes=[1_000_000] * 10,
        today_close=130,  # a huge move well above box_top
        today_high=132,
        today_low=103,
        today_volume=3_000_000,
    )
    box = compute_box("TEST", history=hist)
    assert box is not None
    assert box.breakout is True
