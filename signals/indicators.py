"""Traditional technical-analysis confirmation, independent of any LLM.
Pure functions over a plain closing-price series so the same code works for
stocks (yfinance) and crypto (OKX candles) alike. Advisory context for the
LLM panel by default; only gates a trade if REQUIRE_INDICATOR_CONFIRMATION
is set (see config/settings.py).
"""
import statistics


def rsi(closes: list[float], period: int = 14) -> float | None:
    """Classic (simple-average) RSI. None if there isn't enough history."""
    if len(closes) < period + 1:
        return None
    deltas = [closes[i + 1] - closes[i] for i in range(len(closes) - 1)][-period:]
    gains = [d for d in deltas if d > 0]
    losses = [-d for d in deltas if d < 0]
    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return round(100 - (100 / (1 + rs)), 2)


def _sma(closes: list[float], window: int) -> float | None:
    if len(closes) < window:
        return None
    return sum(closes[-window:]) / window


def sma_trend(closes: list[float], fast: int = 10, slow: int = 30) -> str:
    """"up"/"down" when the fast SMA clears the slow one by >0.1%, else "flat".
    "unknown" if there isn't enough history for the slow window yet."""
    f, s = _sma(closes, fast), _sma(closes, slow)
    if f is None or s is None:
        return "unknown"
    if f > s * 1.001:
        return "up"
    if f < s * 0.999:
        return "down"
    return "flat"


def volatility_regime(closes: list[float], window: int = 20) -> float | None:
    """Stdev of daily returns over the trailing window -- a rough, asset-
    agnostic volatility reading (crypto and stocks have very different
    absolute scales, so this is left unclassified for the caller/LLM to
    interpret in context rather than guessing a universal threshold)."""
    if len(closes) < window + 1:
        return None
    recent = closes[-(window + 1):]
    returns = [(recent[i + 1] - recent[i]) / recent[i] for i in range(len(recent) - 1) if recent[i] != 0]
    if len(returns) < 2:
        return None
    return round(statistics.pstdev(returns), 5)
