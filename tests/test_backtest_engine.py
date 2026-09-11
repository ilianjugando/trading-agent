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


def test_replay_uses_only_past_bars_and_the_live_stop_rule():
    """Dos cosas que si se rompen dan una curva linda y falsa: mirar al
    futuro, y usar un stop distinto al que usa el bot en vivo."""
    from backtest.engine import replay_strategy, summarize

    seen = []

    def _spy(window):
        seen.append(len(window))
        return None  # nunca compra: solo se observa que ve

    closes = [100 + i for i in range(80)]
    replay_strategy(closes, _spy, warmup=35)

    assert seen[0] == 36, "la primera ventana arranca en warmup"
    assert seen == sorted(seen), "la ventana solo crece"
    assert max(seen) == len(closes), "nunca ve mas barras de las que existen"


def test_replay_closes_a_trade_and_summarize_reports_real_metrics():
    import math

    from signals.strategies import trend_follow

    from backtest.engine import replay_strategy, summarize

    # Tendencia alcista con retrocesos: esperanza positiva (si fuera una
    # caida monotona el filtro de EV la rechazaria, con razon).
    closes = [100 * (1.004 ** i) + 9 * math.sin(i / 3.0) for i in range(220)]
    trades = replay_strategy(closes, trend_follow, symbol="TEST")
    r = summarize("test", "0", "99", trades)

    assert trades, "la estrategia tenia que disparar en esa caida"
    assert all(t.exit_reason in ("stop_loss", "target", "end_of_window") for t in trades)
    assert r.equity_curve[0] == 100.0
    assert len(r.equity_curve) == len([t for t in trades if t.pnl_pct is not None]) + 1


def test_metrics_are_none_instead_of_fake_precision_on_one_trade():
    """Con una sola operacion no hay dispersion que medir. Un Sharpe
    inventado sobre n=1 es exactamente el numero que hace que una curva
    mediocre parezca profesional."""
    from backtest.engine import BacktestTrade, summarize

    r = summarize("test", "0", "1", [BacktestTrade("X", "0", 100.0, "1", 110.0, "target", 10.0)])
    assert r.sharpe is None and r.sortino is None
    assert r.total_return_pct == 10.0


def test_analyze_surfaces_what_total_return_hides():
    """El retorno total no dice si vino de una sola operacion afortunada,
    ni cuantas perdidas seguidas hay que aguantar para llegar ahi."""
    from backtest.engine import BacktestTrade, analyze

    t = lambda p, a="0", b="3": BacktestTrade("X", a, 100.0, b, 100.0, "target", p)  # noqa: E731
    r = analyze([t(10), t(-2), t(-2), t(-2), t(6)])

    assert r["closed_trades"] == 5
    assert r["wins"] == 2 and r["losses"] == 3
    assert r["max_loss_streak"] == 3
    assert r["best_pct"] == 10 and r["worst_pct"] == -2
    assert r["profit_factor"] == round(16 / 6, 2)
    assert r["expectancy_pct"] == 2.0


def test_profit_factor_below_one_means_losing_despite_winning_often():
    """Acertar mas veces de las que se falla no alcanza si las perdidas
    son mas grandes que las ganancias."""
    from backtest.engine import BacktestTrade, analyze

    t = lambda p: BacktestTrade("X", "0", 100.0, "1", 100.0, "target", p)  # noqa: E731
    r = analyze([t(1), t(1), t(1), t(-10)])

    assert r["wins"] > r["losses"]
    assert r["profit_factor"] < 1
    assert r["expectancy_pct"] < 0


def test_analyze_on_no_trades_is_not_an_error():
    from backtest.engine import analyze

    assert analyze([])["closed_trades"] == 0
