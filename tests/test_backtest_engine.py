import pandas as pd

from backtest.engine import (
    BacktestTrade,
    _ReplayOKX,
    summarize,
    walk_forward_crypto,
    walk_forward_stocks,
)


def _flat_history(n: int, price: float = 100.0) -> pd.DataFrame:
    idx = pd.date_range("2025-01-01", periods=n, freq="D")
    return pd.DataFrame(
        {"Open": price, "High": price, "Low": price, "Close": price, "Volume": 1000.0}, index=idx
    )


def test_walk_forward_stocks_no_trade_on_flat_series():
    hist = _flat_history(40)
    trades = walk_forward_stocks("FLAT", hist)
    assert trades == []


def test_walk_forward_stocks_opens_on_breakout():
    hist = _flat_history(30, price=100.0)
    # A clean breakout day: much higher close and volume than the box.
    breakout_day = pd.DataFrame(
        {"Open": [100.0], "High": [130.0], "Low": [99.0], "Close": [125.0], "Volume": [5000.0]},
        index=pd.date_range(hist.index[-1] + pd.Timedelta(days=1), periods=1),
    )
    hist = pd.concat([hist, breakout_day])

    trades = walk_forward_stocks("BRK", hist)
    assert len(trades) == 1
    assert trades[0].entry_price == 125.0


def test_walk_forward_stocks_exits_on_stop_hit():
    hist = _flat_history(30, price=100.0)
    breakout_day = pd.DataFrame(
        {"Open": [100.0], "High": [130.0], "Low": [99.0], "Close": [125.0], "Volume": [5000.0]},
        index=pd.date_range(hist.index[-1] + pd.Timedelta(days=1), periods=1),
    )
    crash_day = pd.DataFrame(
        {"Open": [125.0], "High": [125.0], "Low": [50.0], "Close": [60.0], "Volume": [1000.0]},
        index=pd.date_range(breakout_day.index[-1] + pd.Timedelta(days=1), periods=1),
    )
    hist = pd.concat([hist, breakout_day, crash_day])

    trades = walk_forward_stocks("BRK", hist)
    assert len(trades) == 1
    assert trades[0].exit_reason == "stop_loss"
    assert trades[0].pnl_pct is not None


def test_summarize_win_rate_and_drawdown():
    trades = [
        BacktestTrade(symbol="A", entry_date="d1", entry_price=100, exit_date="d2", exit_price=110, pnl_pct=10.0),
        BacktestTrade(symbol="A", entry_date="d3", entry_price=100, exit_date="d4", exit_price=90, pnl_pct=-10.0),
    ]
    result = summarize("stocks", "d1", "d4", trades)
    assert result.win_rate == 50.0
    assert result.total_return_pct == round(100 * 1.10 * 0.90 - 100, 2)
    assert result.max_drawdown_pct > 0


def test_summarize_no_closed_trades():
    result = summarize("stocks", "d1", "d1", [])
    assert result.win_rate is None
    assert result.total_return_pct == 0.0


def test_replay_okx_returns_configured_pct():
    replay = _ReplayOKX({"BTC-USDT": 0.05})
    assert replay.get_24h_change_pct("BTC-USDT") == 0.05


def test_walk_forward_crypto_picks_strongest_momentum():
    # BTC flat, ETH ramps up sharply in the second half.
    closes_by_inst = {
        "BTC-USDT": [100.0] * 48,
        "ETH-USDT": [100.0] * 24 + [100.0 + i for i in range(24)],
    }
    events = walk_forward_crypto(closes_by_inst, bars_per_day_lookback=24)
    assert events
    assert any(e.inst_id == "ETH-USDT" for e in events)


def test_walk_forward_crypto_empty_when_too_short():
    events = walk_forward_crypto({"BTC-USDT": [100.0] * 5}, bars_per_day_lookback=24)
    assert events == []
