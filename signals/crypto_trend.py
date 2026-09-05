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


def rank_universe(okx: OKXAdapter, universe: list[str] = CRYPTO_UNIVERSE) -> list[CryptoSignal]:
    signals = []
    for inst_id in universe:
        try:
            pct = okx.get_24h_change_pct(inst_id)
            signals.append(CryptoSignal(inst_id=inst_id, change_24h_pct=round(pct * 100, 2)))
        except Exception:
            continue
    return sorted(signals, key=lambda s: s.change_24h_pct, reverse=True)
