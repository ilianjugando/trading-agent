from signals.indicators import rsi, sma_trend, volatility_regime


def test_rsi_none_when_not_enough_history():
    assert rsi([1, 2, 3], period=14) is None


def test_rsi_100_when_no_losses():
    closes = [100 + i for i in range(20)]  # straight uptrend, zero down days
    assert rsi(closes, period=14) == 100.0


def test_rsi_low_on_straight_downtrend():
    closes = [100 - i for i in range(20)]
    assert rsi(closes, period=14) < 20


def test_sma_trend_unknown_when_not_enough_history():
    assert sma_trend([1, 2, 3], fast=10, slow=30) == "unknown"


def test_sma_trend_up_on_rising_series():
    closes = [100 + i for i in range(40)]
    assert sma_trend(closes, fast=10, slow=30) == "up"


def test_sma_trend_down_on_falling_series():
    closes = [200 - i for i in range(40)]
    assert sma_trend(closes, fast=10, slow=30) == "down"


def test_volatility_none_when_not_enough_history():
    assert volatility_regime([1, 2, 3], window=20) is None


def test_volatility_higher_for_choppier_series():
    calm = [100 + (i % 2) * 0.1 for i in range(25)]
    choppy = [100 + (i % 2) * 10 for i in range(25)]
    assert volatility_regime(choppy, window=20) > volatility_regime(calm, window=20)
