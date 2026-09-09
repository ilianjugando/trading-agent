"""Tamano de posicion por conviccion y por bucket de riesgo.

Reemplaza la regla anterior, que era `pool_value * max_trade_pct` -- es
decir, apostar SIEMPRE el maximo permitido, 20% del pool, en cada
operacion. Con $1M en la cuenta eso son $200.000 por trade, y explica por
que el panel de modelos tenia que ser tan conservador: cuando cada "si"
cuesta el 20% del capital, exigir casi certeza es la respuesta correcta.

El problema no era la cautela del panel sino que no existia la posicion
chica. Una apuesta especulativa de 0,3% del portafolio era literalmente
inexpresable, y con ella toda la estrategia de asimetria: muchas
posiciones pequenas que pueden fallar, sostenidas por las pocas que
salen muy bien.

Importante sobre riesgo: `SpendGuard` impone `max_trade_pct` como TECHO,
no como piso (risk/spend_guard.py). Nada de lo que hay aca afloja un
limite; al contrario, todos los tamanos que produce son menores o iguales
al que el sistema ya usaba. El techo duro sigue siendo la ultima palabra
y se aplica igual, despues de esto.
"""
from dataclasses import dataclass

# Techo por bucket, como fraccion del pool. Son limites superiores: el
# tamano real escala hacia abajo con la conviccion.
#
# core      -- tesis de mayor certeza, activos liquidos y grandes.
# momentum  -- operativa tactica normal, el caso habitual.
# moonshot  -- apuesta asimetrica especulativa. El 0,5% esta elegido para
#              que una racha de 10 fallos seguidos cueste ~5% del pool:
#              doloroso pero sobrevivible, que es la condicion para poder
#              seguir jugando hasta que aparezca la que multiplica.
BUCKET_CAPS = {
    "core": 0.10,
    "momentum": 0.03,
    "moonshot": 0.005,
}

# Debajo de esto la orden no vale la pena: comisiones y minimos de lote se
# comen el resultado, y en OKX una orden demasiado chica es rechazada.
MIN_ORDER_USD = 15.0


@dataclass
class PositionSize:
    usd: float
    pct_of_pool: float
    bucket: str
    reason: str
    viable: bool  # False cuando queda por debajo del minimo ejecutable


def size_position(
    pool_value: float,
    bucket: str,
    conviction: float,
    max_trade_pct: float,
    expected_value_pct: float | None = None,
) -> PositionSize:
    """`conviction` en [0, 1] -- tipicamente la confianza del panel
    combinada con la calidad de la asimetria.

    El tamano es lineal en la conviccion en vez de todo-o-nada: una senal
    de conviccion 0,4 entra con menos capital, no se descarta. Ahi esta la
    diferencia con el sistema anterior, donde por debajo del umbral no
    entraba nada y por encima entraba el maximo.
    """
    if bucket not in BUCKET_CAPS:
        raise ValueError(f"Bucket desconocido: {bucket!r}. Validos: {sorted(BUCKET_CAPS)}")
    if pool_value <= 0:
        raise ValueError(f"pool_value invalido: {pool_value}")

    conviction = max(0.0, min(1.0, conviction))
    cap_pct = min(BUCKET_CAPS[bucket], max_trade_pct)

    pct = cap_pct * conviction
    # Esperanza negativa no se opera, por muy convencido que este el panel:
    # el tamano correcto de una apuesta con expectativa negativa es cero.
    if expected_value_pct is not None and expected_value_pct <= 0:
        return PositionSize(0.0, 0.0, bucket, f"esperanza negativa ({expected_value_pct:.2f}%)", viable=False)

    usd = pool_value * pct
    if usd < MIN_ORDER_USD:
        return PositionSize(
            round(usd, 2), round(pct * 100, 4), bucket,
            f"${usd:.2f} por debajo del minimo ejecutable de ${MIN_ORDER_USD:.0f}", viable=False,
        )

    return PositionSize(
        usd=round(usd, 2),
        pct_of_pool=round(pct * 100, 4),
        bucket=bucket,
        reason=f"{bucket} al {conviction:.0%} de conviccion (techo {cap_pct:.1%})",
        viable=True,
    )


def classify_bucket(size_bucket: str | None, reward_risk: float | None) -> str:
    """A que bucket pertenece un candidato.

    `size_bucket` es la capitalizacion cuando se conoce ("large", "mid",
    "small", "micro"); None para activos sin ese dato, que se tratan como
    el caso intermedio en vez de asumir lo mejor o lo peor.

    Una micro cap va a moonshot aunque la senal se vea inmejorable: el
    riesgo ahi no es la calidad de la senal sino la imposibilidad de salir
    del activo, y eso no se arregla teniendo razon.
    """
    if size_bucket == "micro":
        return "moonshot"
    if size_bucket == "large" and (reward_risk is None or reward_risk < 3.0):
        return "core"
    if size_bucket == "small" and reward_risk is not None and reward_risk >= 4.0:
        return "moonshot"
    return "momentum"
