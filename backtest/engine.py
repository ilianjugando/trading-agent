"""Backtest engine: replays the *existing* deterministic signal functions
(compute_box, rank_universe) over historical data. No LLM calls, no live
orders -- results are an UPPER BOUND on trade frequency/returns, since
the live LLM panel can only reduce how many of these signals actually
execute, never add to them.
"""
import statistics
from dataclasses import dataclass, field

import pandas as pd

from signals import asymmetry
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
    sharpe: float | None = None
    sortino: float | None = None
    calmar: float | None = None
    equity_curve: list[float] = field(default_factory=list)


def replay_strategy(closes: list[float], strategy_fn, symbol: str = "?",
                    highs: list[float] | None = None, lows: list[float] | None = None,
                    warmup: int = 35) -> list[BacktestTrade]:
    """Replays any signals/strategies.py function bar by bar.

    El objetivo y el stop NO son parametros propios: salen de
    asymmetry.compute() sobre la historia disponible hasta esa barra, que
    es exactamente la regla que usa el bot en vivo
    (orchestrator: stop = entry * (1 - asym.stop_pct/100)). Asi el
    backtest mide la estrategia real y no una variante inventada para el
    backtest -- que es como se consigue una curva linda que despues no
    aparece operando.

    Solo ve `closes[:i+1]` en cada paso: nada de mirar al futuro.
    """
    trades: list[BacktestTrade] = []
    open_trade: BacktestTrade | None = None
    stop = target = None

    for i in range(warmup, len(closes)):
        window = closes[: i + 1]
        price = window[-1]

        if open_trade is not None:
            if price <= stop:
                reason, exit_price = "stop_loss", stop
            elif price >= target:
                reason, exit_price = "target", target
            else:
                continue
            open_trade.exit_date = str(i)
            open_trade.exit_price = round(exit_price, 8)
            open_trade.exit_reason = reason
            open_trade.pnl_pct = round((exit_price - open_trade.entry_price) / open_trade.entry_price * 100, 2)
            trades.append(open_trade)
            open_trade = None
            continue

        if strategy_fn(window) is None:
            continue
        asym = asymmetry.compute(
            window,
            highs=highs[: i + 1] if highs else None,
            lows=lows[: i + 1] if lows else None,
        )
        if asym is None or asym.expected_value_pct <= 0:
            continue  # mismo filtro que el scanner en vivo
        open_trade = BacktestTrade(symbol=symbol, entry_date=str(i), entry_price=price)
        stop = price * (1 - asym.stop_pct / 100)
        target = price * (1 + asym.target_pct / 100)

    if open_trade is not None:
        last = closes[-1]
        open_trade.exit_date = str(len(closes) - 1)
        open_trade.exit_price = last
        open_trade.exit_reason = "end_of_window"
        open_trade.pnl_pct = round((last - open_trade.entry_price) / open_trade.entry_price * 100, 2)
        trades.append(open_trade)

    return trades


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
    curve = [equity]
    for t in closed:
        equity *= 1 + t.pnl_pct / 100
        peak = max(peak, equity)
        max_dd = max(max_dd, (peak - equity) / peak * 100)
        curve.append(round(equity, 4))

    # Sharpe/Sortino por OPERACION, no anualizados: anualizar exige asumir
    # una frecuencia de trading que un backtest de 40 operaciones no
    # sostiene, y el numero inflado es justo el que hace que una curva
    # mediocre parezca profesional. Con menos de 2 operaciones cerradas no
    # hay dispersion que medir y se devuelve None en vez de un numero que
    # aparenta precision inexistente.
    rets = [t.pnl_pct for t in closed]
    sharpe = sortino = calmar = None
    if len(rets) >= 2:
        mean = statistics.fmean(rets)
        sd = statistics.pstdev(rets)
        sharpe = round(mean / sd, 2) if sd > 0 else None
        downside = statistics.pstdev([min(r, 0.0) for r in rets])
        sortino = round(mean / downside, 2) if downside > 0 else None
        if max_dd > 0:
            calmar = round((equity - 100.0) / max_dd, 2)

    return BacktestResult(
        pool=pool,
        window_start=window_start,
        window_end=window_end,
        trades=trades,
        win_rate=win_rate,
        total_return_pct=round(equity - 100.0, 2),
        max_drawdown_pct=round(max_dd, 2),
        sharpe=sharpe,
        sortino=sortino,
        calmar=calmar,
        equity_curve=curve,
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


def analyze(trades: list[BacktestTrade]) -> dict:
    """Las estadisticas que dicen si una estrategia sirve, mas alla del
    retorno total.

    El retorno total esconde casi todo: no dice si viene de una sola
    operacion afortunada, si las ganadoras compensan a las perdedoras, ni
    cuantas perdidas seguidas hay que aguantar para llegar ahi.
    """
    closed = [t for t in trades if t.pnl_pct is not None]
    if not closed:
        return {"closed_trades": 0}

    wins = [t.pnl_pct for t in closed if t.pnl_pct > 0]
    losses = [t.pnl_pct for t in closed if t.pnl_pct <= 0]

    # Rachas: cuantas perdidas seguidas hay que tolerar. Una estrategia
    # rentable con 8 perdidas al hilo se abandona antes de que funcione.
    best_streak = worst_streak = cur = 0
    for t in closed:
        if t.pnl_pct > 0:
            cur = cur + 1 if cur > 0 else 1
            best_streak = max(best_streak, cur)
        else:
            cur = cur - 1 if cur < 0 else -1
            worst_streak = min(worst_streak, cur)

    gross_win = sum(wins)
    gross_loss = abs(sum(losses))
    holding = [
        int(t.exit_date) - int(t.entry_date)
        for t in closed
        if str(t.entry_date).isdigit() and str(t.exit_date or "").isdigit()
    ]

    return {
        "closed_trades": len(closed),
        "wins": len(wins),
        "losses": len(losses),
        "avg_win_pct": round(statistics.fmean(wins), 2) if wins else None,
        "avg_loss_pct": round(statistics.fmean(losses), 2) if losses else None,
        "best_pct": round(max(t.pnl_pct for t in closed), 2),
        "worst_pct": round(min(t.pnl_pct for t in closed), 2),
        # Cuanto gana por cada unidad que pierde. Debajo de 1 la estrategia
        # pierde plata aunque acierte mas veces de las que falla.
        "profit_factor": round(gross_win / gross_loss, 2) if gross_loss > 0 else None,
        # Lo que deja una operacion promedio. Es el numero que importa
        # cuando se repite muchas veces.
        "expectancy_pct": round(statistics.fmean([t.pnl_pct for t in closed]), 3),
        "max_win_streak": best_streak,
        "max_loss_streak": abs(worst_streak),
        "avg_bars_held": round(statistics.fmean(holding), 1) if holding else None,
    }
