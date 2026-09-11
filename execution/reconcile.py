"""Reconciliacion de posiciones contra el broker/exchange (seccion 13).

El sistema interno nunca puede asumir que tiene la verdad absoluta. Lo que
el bot cree tener y lo que la cuenta realmente tiene pueden separarse por
motivos que ya se vieron en vivo, todos el mismo dia (2026-09-10):

  - una orden que se cancelo sin llenarse pero quedo registrada como
    ejecutada (4 posiciones fantasma en OKX);
  - un llenado parcial contabilizado como completo (3 posiciones, hasta
    57% de diferencia);
  - comisiones cobradas en la moneda comprada, que dejan menos unidades de
    las estimadas;
  - operaciones hechas por fuera del bot.

Una posicion que el bot cree tener y no tiene es una alerta de riesgo
distinta de la habitual: su stop-loss no protege nada, y al dispararse la
venta se rechaza. Una que tiene y no sabe que tiene es peor: no tiene stop
en absoluto.

Este modulo solo COMPARA y REPORTA. No corrige nada por su cuenta: una
correccion automatica y silenciosa del estado es como se pierde el rastro
de lo que realmente paso.
"""
from dataclasses import dataclass, field

# Diferencia relativa por debajo de la cual no se reporta nada. Existe
# porque las comisiones dejan siempre un resto de polvo: reportar 0,05%
# cada ciclo entrenaria a ignorar la alerta, que es como una alerta deja
# de servir.
TOLERANCE_PCT = 0.5


@dataclass
class Reconciliation:
    pool: str
    phantom: dict = field(default_factory=dict)      # el bot cree tenerlas, la cuenta no
    untracked: dict = field(default_factory=dict)    # la cuenta las tiene, el bot no
    mismatched: dict = field(default_factory=dict)   # ambos, con cantidades distintas

    @property
    def clean(self) -> bool:
        return not (self.phantom or self.untracked or self.mismatched)

    def as_log_record(self) -> dict:
        return {
            "pool": self.pool,
            "result": "position_reconciliation_alert",
            "phantom": self.phantom,
            "untracked": self.untracked,
            "mismatched": self.mismatched,
            "detail": "el estado del bot y la cuenta real no coinciden -- no se corrigio nada automaticamente",
        }


def compare(pool: str, tracked: dict, broker_qty: dict) -> Reconciliation:
    """`tracked` es positions_<pool>.json; `broker_qty` es {simbolo: qty}
    tal como lo reporta el broker. Los simbolos ya deben venir en la misma
    convencion de nombres."""
    result = Reconciliation(pool=pool)

    for symbol, pos in tracked.items():
        expected = float(pos.get("qty", 0.0))
        actual = float(broker_qty.get(symbol, 0.0))
        if actual <= 0:
            result.phantom[symbol] = expected
        elif expected > 0 and abs(expected - actual) / expected * 100 > TOLERANCE_PCT:
            result.mismatched[symbol] = {"tracked": expected, "real": actual}

    for symbol, actual in broker_qty.items():
        if float(actual) > 0 and symbol not in tracked:
            result.untracked[symbol] = float(actual)

    return result
