"""Radar de mercado cripto mas amplio que OKX -- puramente informativo.

OKX solo lista ~243 pares. Los movimientos de "$12 -> $1200" casi siempre
pasan antes en lugares que OKX no cubre: exchanges centralizados chicos
(via el screener de TradingView, que agrega decenas de exchanges) y sobre
todo pares de DEX (PancakeSwap, Raydium, Uniswap...), donde los meme coins
nacen antes de llegar a cualquier exchange grande.

Esto NO es una fuente de trading. No alimenta opportunity_scanner.py, no
llega al panel LLM, no puede disparar una compra -- es deliberadamente
solo lectura para mostrar en el dashboard. La razon es de riesgo, no
tecnica: comprar en un DEX requiere manejar una wallet con clave privada y
ningun filtro anti-estafa (liquidez bloqueada, concentracion de holders,
si el contrato deja vender) existe hoy en este proyecto. Conectar esto a
ejecucion real es una decision aparte, todavia no tomada.

Usa `tradingview-screener` (MIT, no oficial -- envuelve la API interna del
screener de TradingView, puede romperse si TradingView la cambia sin
aviso). Reutiliza los screeners `crypto()`/`crypto_dex()` ya armados por
la libreria en vez de reconstruir las columnas a mano.
"""
from dataclasses import dataclass

from tradingview_screener import Column as col
from tradingview_screener.screeners import crypto, crypto_dex

# Piso de volumen para no mostrar ruido de pares con casi nada de liquidez
# real detras del numero de cambio %.
_MIN_CEX_VOLUME_USD = 500_000
_MIN_DEX_VOLUME_USD = 50_000

# Un par de DEX recien creado puede mostrar un cambio % sin sentido (miles
# de %) porque el precio de referencia previo era casi cero -- no es un
# dato falso, pero mostrarlo tal cual confunde mas de lo que informa. Se
# marca en vez de esconderse.
_SUSPICIOUS_CHANGE_PCT = 1000.0


@dataclass
class Mover:
    symbol: str
    exchange: str
    price: float
    change_24h_pct: float
    volume_24h_usd: float
    suspicious: bool  # cambio % fuera de todo rango creible -- ver arriba


@dataclass
class DexMover(Mover):
    blockchain: str


def cex_movers(min_change_pct: float = 15.0, limit: int = 20) -> list[Mover]:
    """Los que mas se movieron en las ultimas 24h, agregando decenas de
    exchanges centralizados via TradingView -- no solo OKX. Es
    informativo: no implica que el par exista en OKX ni que se pueda
    operar hoy."""
    q = crypto().where(
        col("24h_close_change|5") > min_change_pct,
        col("24h_vol|5") > _MIN_CEX_VOLUME_USD,
    ).order_by("24h_close_change|5", ascending=False)
    q.set_property("range", [0, limit])
    _, df = q.get_scanner_data()

    return [
        Mover(
            symbol=row["ticker"],
            exchange=row["exchange.tr"],
            price=float(row["close"]),
            change_24h_pct=round(float(row["24h_close_change|5"]), 2),
            volume_24h_usd=round(float(row["24h_vol|5"]), 2),
            suspicious=abs(row["24h_close_change|5"]) > _SUSPICIOUS_CHANGE_PCT,
        )
        for _, row in df.iterrows()
    ]


def dex_movers(min_change_pct: float = 20.0, limit: int = 20) -> list[DexMover]:
    """Pares de exchanges descentralizados moviendose fuerte -- donde
    realmente nacen los meme coins antes de listarse en cualquier lado.
    Puramente informativo, ver docstring del modulo."""
    q = crypto_dex().where(
        col("24h_close_change|5") > min_change_pct,
        col("dex_trading_volume_24h") > _MIN_DEX_VOLUME_USD,
    ).order_by("24h_close_change|5", ascending=False)
    q.set_property("range", [0, limit])
    _, df = q.get_scanner_data()

    return [
        DexMover(
            symbol=row["ticker"],
            exchange=row["exchange.tr"],
            blockchain=row["blockchain-id.tr"],
            price=float(row["close"]),
            change_24h_pct=round(float(row["24h_close_change|5"]), 2),
            volume_24h_usd=round(float(row["dex_trading_volume_24h"]), 2),
            suspicious=abs(row["24h_close_change|5"]) > _SUSPICIOUS_CHANGE_PCT,
        )
        for _, row in df.iterrows()
    ]
