"""Momentum ranking over a configured crypto universe using OKX market
data only (numeric), for the same reason stock_trend.py avoids text feeds.
"""
from dataclasses import dataclass

from brokers.okx_adapter import OKXAdapter
from config.universe import CRYPTO_UNIVERSE


@dataclass
class CryptoSignal:
    inst_id: str
    change_24h_pct: float


# Pairs that never produce a momentum signal by design -- a stablecoin
# quoted against a stablecoin sits at 0% forever and would only ever add
# noise to the ranking.
_STABLES = {"USDC", "USDT", "DAI", "TUSD", "FDUSD", "USDD", "PYUSD", "EURT"}


def liquid_universe(okx: OKXAdapter, top_n: int = 25, min_volume_usd: float = 5_000_000) -> list[str]:
    """The most-traded USDT spot pairs right now.

    A hardcoded 5-coin list leaves the scan blind to a real move in
    anything not on it (observed: the list's best mover was +0.5% on a
    day another liquid pair ran +25%). Ranking live liquidity instead
    means the universe follows the market, and the volume floor keeps
    thin/illiquid listings -- where a "signal" is really just a wide
    spread -- out of it."""
    tickers = [t for t in okx.get_spot_tickers() if t["volume_24h_usd"] >= min_volume_usd]
    tickers = [t for t in tickers if t["inst_id"].split("-")[0] not in _STABLES]
    tickers.sort(key=lambda t: t["volume_24h_usd"], reverse=True)
    return [t["inst_id"] for t in tickers[:top_n]]


def rank_universe(
    okx: OKXAdapter,
    universe: list[str] = CRYPTO_UNIVERSE,
    min_change_pct: float = float("-inf"),
) -> list[CryptoSignal]:
    """Strongest 24h movers first. `min_change_pct` is a floor on what
    counts as momentum at all: without it the top of a flat market (a
    +0.5% drift) is proposed as a trade every single run, which is noise
    dressed up as a signal -- and it pushed the whole judgement call onto
    the LLM panel instead of filtering deterministically first."""
    signals = []
    for inst_id in universe:
        try:
            pct = okx.get_24h_change_pct(inst_id)
            signals.append(CryptoSignal(inst_id=inst_id, change_24h_pct=round(pct * 100, 2)))
        except Exception:
            continue
    ranked = sorted(signals, key=lambda s: s.change_24h_pct, reverse=True)
    return [s for s in ranked if s.change_24h_pct >= min_change_pct]
