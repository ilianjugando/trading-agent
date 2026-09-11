"""The competing strategies, as pure functions over a price history.

Each one answers the same question -- "would you buy this right now?" --
from the same input, so their track records are directly comparable.
That comparability is the whole point: the system runs all of them in
shadow mode, records what each *would* have done, and scores them
against what the price actually did (see execution/tournament.py). None
of these place orders; they produce evidence.

All are long-only and deliberately simple. A strategy that needs tuning
to look good in a backtest is a strategy that will disappoint live.
"""
from dataclasses import dataclass

from signals.indicators import _sma, rsi, sma_trend


@dataclass
class Proposal:
    strategy: str
    reason: str
    entry_price: float


def momentum(closes: list[float], min_change_pct: float = 3.0) -> Proposal | None:
    """Buy what's already running. Rides continuation, gets hurt by
    mean reversion -- the opposite bet to `mean_reversion` below, which
    is exactly why both are in the tournament."""
    if len(closes) < 2 or closes[-2] <= 0:
        return None
    change = (closes[-1] - closes[-2]) / closes[-2] * 100
    if change < min_change_pct:
        return None
    return Proposal("momentum", f"+{change:.1f}% en la ultima barra", closes[-1])


def mean_reversion(closes: list[float], oversold: float = 30.0) -> Proposal | None:
    """Buy what's been beaten down, betting it snaps back. Wins in
    range-bound markets, catches falling knives in real downtrends."""
    value = rsi(closes)
    if value is None or value > oversold:
        return None
    return Proposal("mean_reversion", f"RSI {value:.1f} en sobreventa", closes[-1])


def trend_follow(closes: list[float]) -> Proposal | None:
    """Buy established uptrends, not fresh moves. Slower to enter than
    momentum, and slower to be wrong."""
    if sma_trend(closes) != "up":
        return None
    fast = _sma(closes, 10)
    if fast is None or closes[-1] <= fast:
        return None
    return Proposal("trend_follow", "tendencia alcista, precio sobre SMA10", closes[-1])


def breakout(closes: list[float], window: int = 20) -> Proposal | None:
    """Buy a clean break of the recent ceiling -- the crypto-side cousin
    of the Darvas box the stock leg already trades."""
    if len(closes) < window + 1:
        return None
    prior_high = max(closes[-(window + 1):-1])
    if closes[-1] <= prior_high:
        return None
    return Proposal("breakout", f"ruptura del maximo de {window} barras", closes[-1])


# Registry. Adding a strategy here is all it takes to enter it in the
# tournament -- the runner iterates this, nothing is hardcoded downstream.
ALL_STRATEGIES = {
    "momentum": momentum,
    "mean_reversion": mean_reversion,
    "trend_follow": trend_follow,
    "breakout": breakout,
}


def evaluate_all(closes: list[float], on_error=None, extra: dict | None = None) -> list[Proposal]:
    """Every strategy's verdict on one symbol. Strategies that decline
    simply don't appear -- a quiet strategy is a valid outcome, and
    forcing every one to fire on every bar is how you get noise.

    Una estrategia que LEVANTA una excepcion no es lo mismo que una que
    declina, aunque el resultado inmediato se vea igual (no aparece en la
    lista). Antes ambos casos eran indistinguibles: una estrategia rota
    bajaba el puntaje de confluencia de todo el universo en silencio, y
    parecia simplemente "no disparo". `on_error(nombre, excepcion)` deja
    que el llamador lo registre; sin el, el comportamiento es el de antes.
    """
    out = []
    # `extra` son las estrategias custom (signals/custom.py): mismas
    # funciones, mismo tratamiento, misma puntuacion de confluencia.
    for name, fn in {**ALL_STRATEGIES, **(extra or {})}.items():
        try:
            proposal = fn(closes)
        except Exception as e:
            if on_error is not None:
                on_error(name, e)
            continue
        if proposal is not None:
            out.append(proposal)
    return out
