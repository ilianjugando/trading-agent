"""Descubrimiento de oportunidades: escanear todo, puntuar todo, rankear.

Reemplaza el embudo anterior, que era una cadena de filtros booleanos que
colapsaba a UN ganador. Medido sobre datos reales: de 243 pares de OKX
llegaba 1 solo al panel de decision, y de 20 acciones tambien 1. Los
demas se descartaban sin dejar registro de por que.

Dos cambios de fondo respecto de eso:

1. Los filtros PUNTUAN en vez de eliminar. Un RSI de 71 baja el score, no
   descalifica. Asi un activo con asimetria excepcional puede competir
   aunque tenga una metrica fea, y uno sin ningun atractivo queda abajo
   aunque no viole ningun umbral. Solo se rechaza de plano lo que no se
   puede operar o lo que tiene esperanza negativa.

2. Todo rechazo queda registrado con su motivo y su valor numerico. Sin
   eso es imposible distinguir "no habia oportunidades" de "el sistema no
   fue capaz de encontrarlas", que son dos cosas completamente distintas
   y se veian identicas en el log.

Este modulo no llama a ningun LLM ni coloca ordenes: produce una lista
rankeada de candidatos para que el panel revise los mejores.
"""
from dataclasses import dataclass, field

from signals import asymmetry as asymmetry_mod
from signals.indicators import rsi, sma_trend
from signals.strategies import evaluate_all

# Solo estas condiciones eliminan un candidato. Todo lo demas puntua.
REJECT_INSUFFICIENT_DATA = "datos insuficientes"
REJECT_NO_ASYMMETRY = "no se pudo medir asimetria"
REJECT_NEGATIVE_EV = "esperanza matematica negativa"


@dataclass
class Candidate:
    symbol: str
    score: float
    asymmetry: asymmetry_mod.Asymmetry | None
    strategies: list[str] = field(default_factory=list)
    rejected_because: str | None = None
    metrics: dict = field(default_factory=dict)

    @property
    def accepted(self) -> bool:
        return self.rejected_because is None


@dataclass
class ScanResult:
    scanned: int
    shortlist: list[Candidate]  # aceptados, mejor score primero
    rejected: list[Candidate]
    rejection_summary: dict[str, int]
    # Estrategias que LEVANTARON excepcion (no las que simplemente
    # declinaron): una rota es un bug, no una senal ausente.
    strategy_errors: dict = field(default_factory=dict)

    def as_metrics(self) -> dict:
        """Las metricas del ciclo, para el log de decisiones."""
        return {
            "assets_scanned": self.scanned,
            "assets_shortlisted": len(self.shortlist),
            "assets_rejected": len(self.rejected),
            "rejection_reasons": self.rejection_summary,
            "top_candidates": [
                {"symbol": c.symbol, "score": c.score,
                 "ev_pct": c.asymmetry.expected_value_pct if c.asymmetry else None}
                for c in self.shortlist[:20]
            ],
            # Sin esto, "por que el bot no compro X" solo se podia responder
            # con el conteo agregado -- nunca con el activo puntual. Se
            # trunca a 40 por motivo para no inflar decisions.log sin limite
            # en un universo grande.
            "strategy_errors": self.strategy_errors,
            "rejected_symbols": {
                reason: [c.symbol for c in self.rejected if c.rejected_because == reason][:40]
                for reason in self.rejection_summary
            },
        }


def _score(asym: asymmetry_mod.Asymmetry, strategies: list[str], rsi_value: float | None, trend: str) -> tuple[float, dict]:
    """Score 0-100 compuesto. Se devuelve tambien el desglose para que una
    decision se pueda auditar despues sin tener que reconstruirla."""
    parts = {}

    # La esperanza matematica manda: es lo unico que responde "conviene
    # tomar esta apuesta muchas veces?". 2% de EV por operacion ya es
    # excelente, de ahi la escala.
    parts["expectativa"] = max(0.0, min(40.0, asym.expected_value_pct * 20))

    # Asimetria pura: cuanto se busca por cada unidad arriesgada.
    parts["asimetria"] = max(0.0, min(20.0, (asym.reward_risk - 1) * 6))

    # Confluencia: que varias estrategias independientes coincidan es
    # evidencia mas fuerte que una sola disparando.
    parts["confluencia"] = min(20.0, len(strategies) * 7.0)

    # Probabilidad de acierto, con menos peso que la asimetria a proposito:
    # acertar poco y ganar mucho es un resultado valido.
    parts["acierto"] = asym.win_prob * 10

    # Calidad de la evidencia: una probabilidad medida sobre 15 ventanas
    # vale menos que una medida sobre 150.
    parts["muestra"] = min(10.0, asym.sample_size / 15)

    penalty = 0.0
    if rsi_value is not None and rsi_value >= 70:
        # Penaliza, no elimina: comprar extendido es peor entrada, pero si
        # la asimetria compensa el candidato merece competir.
        penalty += min(15.0, (rsi_value - 70) * 0.75)
    if trend == "down":
        penalty += 8.0
    parts["penalizacion"] = -penalty

    total = max(0.0, min(100.0, sum(parts.values())))
    return round(total, 2), {k: round(v, 2) for k, v in parts.items()}


def scan(price_data: dict[str, dict], min_expected_value_pct: float = 0.0,
         extra_strategies: dict | None = None) -> ScanResult:
    """Puntua todo el universo.

    `price_data[symbol]` acepta: `closes` (obligatorio), y opcionalmente
    `highs`, `lows`, `size_bucket`, y cualquier metadata extra que se
    quiera arrastrar al payload.

    Levanta excepcion si la estructura de entrada es invalida -- eso es un
    bug del llamador. Un activo individual sin datos suficientes no es un
    error: se registra como rechazado con su motivo.
    """
    if not isinstance(price_data, dict):
        raise TypeError(f"price_data debe ser dict, se recibio {type(price_data).__name__}")

    shortlist: list[Candidate] = []
    rejected: list[Candidate] = []
    summary: dict[str, int] = {}
    # Una estrategia que se rompe baja el puntaje de confluencia de todo
    # el universo sin que se note. Se cuenta por estrategia y viaja en
    # as_metrics() hasta decisions.log, que es el unico canal visible en
    # produccion (la tarea programada corre pythonw.exe, sin consola).
    strategy_errors: dict[str, str] = {}

    def _note_strategy_error(name, exc):
        strategy_errors[name] = f'{type(exc).__name__}: {exc}'

    def reject(symbol: str, reason: str, metrics: dict | None = None) -> None:
        rejected.append(Candidate(symbol=symbol, score=0.0, asymmetry=None,
                                  rejected_because=reason, metrics=metrics or {}))
        summary[reason] = summary.get(reason, 0) + 1

    for symbol, data in price_data.items():
        closes = data.get("closes") or []
        if len(closes) < 35:
            reject(symbol, REJECT_INSUFFICIENT_DATA, {"barras": len(closes)})
            continue

        asym = asymmetry_mod.compute(closes, highs=data.get("highs"), lows=data.get("lows"))
        if asym is None:
            reject(symbol, REJECT_NO_ASYMMETRY, {"barras": len(closes)})
            continue

        if asym.expected_value_pct <= min_expected_value_pct:
            reject(symbol, REJECT_NEGATIVE_EV, {
                "ev_pct": asym.expected_value_pct,
                "reward_risk": asym.reward_risk,
                "win_prob": asym.win_prob,
            })
            continue

        strategies = [p.strategy for p in evaluate_all(closes, on_error=_note_strategy_error, extra=extra_strategies)]
        rsi_value = rsi(closes)
        trend = sma_trend(closes)
        score, breakdown = _score(asym, strategies, rsi_value, trend)

        shortlist.append(Candidate(
            symbol=symbol,
            score=score,
            asymmetry=asym,
            strategies=strategies,
            metrics={
                "rsi_14": rsi_value,
                "sma_trend": trend,
                "score_breakdown": breakdown,
                "size_bucket": data.get("size_bucket"),
                **{k: v for k, v in data.items() if k not in ("closes", "highs", "lows")},
            },
        ))

    shortlist.sort(key=lambda c: c.score, reverse=True)
    return ScanResult(
        scanned=len(price_data),
        shortlist=shortlist,
        rejected=rejected,
        rejection_summary=summary,
        strategy_errors=strategy_errors,
    )
