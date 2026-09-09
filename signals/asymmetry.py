"""Riesgo/beneficio asimetrico: cuanto se puede ganar, cuanto se puede
perder, y con que frecuencia historica paso cada cosa en este activo.

El sistema viejo decidia con un booleano ("hay senal / no hay senal"), lo
que obliga a exigir casi certeza antes de entrar. Este modulo produce en
cambio una expectativa matematica, que permite tomar una posicion
*pequena* en algo improbable pero muy asimetrico: una apuesta con 30% de
acierto y 5:1 de recompensa/riesgo tiene expectativa positiva y el
sistema tiene que poder verlo.

Como se estima la probabilidad -- esto importa, porque es facil inventar
un numero que suene bien:

Se simula sobre la historia real del propio activo con el metodo de doble
barrera. Para cada barra pasada se pregunta "si hubiera entrado aca, el
precio toco primero el objetivo o primero el stop, dentro de N barras?".
La frecuencia resultante es un dato medido, no una suposicion. Se reporta
siempre junto al tamano de muestra, porque una probabilidad sobre 12
observaciones no es lo mismo que sobre 200.

Limitacion conocida y deliberadamente no escondida: las ventanas se
solapan, asi que las muestras estan correlacionadas entre si y el tamano
de muestra efectivo es menor que el nominal. Sirve para comparar activos
entre si bajo el mismo criterio, no como una probabilidad calibrada en
sentido estricto. `sample_size` esta expuesto justamente para que quien
lea el numero pueda descontarlo.
"""
import statistics
from dataclasses import dataclass


@dataclass
class Asymmetry:
    target_pct: float  # objetivo al alza, en %
    stop_pct: float  # riesgo a la baja, en % (magnitud positiva)
    reward_risk: float  # target / stop
    win_prob: float  # frecuencia de aciertos entre casos resueltos, 0-1
    expected_value_pct: float  # esperanza matematica por operacion, en %
    volatility_pct: float  # volatilidad tipica de una barra, en %
    sample_size: int  # cuantas ventanas historicas se pudieron evaluar
    resolved_pct: float  # % de esas ventanas que toco una barrera (el resto expiro)


def _typical_move_pct(closes: list[float], highs: list[float] | None, lows: list[float] | None, window: int) -> float | None:
    """Movimiento tipico de una barra, en %. Usa el rango real (high-low)
    cuando esta disponible porque captura las mechas -- un activo puede
    cerrar plano habiendo recorrido 15% intradia, y ese recorrido es
    exactamente lo que decide si un stop se toca o no. Sin OHLC completo
    cae a la variacion entre cierres, que subestima el rango real."""
    if highs and lows and len(highs) == len(lows) == len(closes):
        ranges = [
            (h - l) / c * 100
            for h, l, c in zip(highs[-window:], lows[-window:], closes[-window:])
            if c > 0 and h >= l
        ]
        if ranges:
            return statistics.fmean(ranges)

    recent = closes[-(window + 1):]
    moves = [
        abs(recent[i + 1] - recent[i]) / recent[i] * 100
        for i in range(len(recent) - 1)
        if recent[i] > 0
    ]
    return statistics.fmean(moves) if moves else None


def _barrier_outcomes(closes: list[float], target_pct: float, stop_pct: float, horizon: int) -> tuple[int, int, int]:
    """Recorre la historia preguntando, en cada punto de entrada posible,
    que barrera se toco primero dentro de `horizon` barras.
    Devuelve (aciertos, perdidas, expirados)."""
    wins = losses = timeouts = 0
    for i in range(len(closes) - 1):
        entry = closes[i]
        if entry <= 0:
            continue
        up = entry * (1 + target_pct / 100)
        down = entry * (1 - stop_pct / 100)
        outcome = None
        for j in range(i + 1, min(i + 1 + horizon, len(closes))):
            if closes[j] >= up:
                outcome = "win"
                break
            if closes[j] <= down:
                outcome = "loss"
                break
        if outcome == "win":
            wins += 1
        elif outcome == "loss":
            losses += 1
        else:
            timeouts += 1
    return wins, losses, timeouts


def _structural_levels(closes: list[float], highs: list[float] | None, lows: list[float] | None,
                       window: int, typical: float) -> tuple[float, float]:
    """Objetivo y stop leidos de la estructura real del precio: la
    resistencia de arriba y el soporte de abajo.

    Derivarlos de multiplos fijos de volatilidad daria la misma relacion
    recompensa/riesgo para todos los activos -- constante, o sea inutil
    para comparar entre candidatos, que es justo lo que hay que hacer.
    Leyendolos de maximos y minimos recientes, un activo que acaba de
    rebotar en un piso con mucho recorrido libre hasta el techo muestra
    una asimetria genuinamente distinta a uno pegado a su resistencia.

    Cuando la estructura no sirve (precio ya en maximos, o niveles
    absurdamente lejanos) se cae a la volatilidad tipica, que siempre da
    un numero razonable.
    """
    price = closes[-1]
    recent_highs = highs[-window:] if highs and len(highs) >= window else closes[-window:]
    recent_lows = lows[-window:] if lows and len(lows) >= window else closes[-window:]

    resistance = max(recent_highs)
    support = min(recent_lows)

    target_pct = (resistance - price) / price * 100 if price > 0 else 0.0
    stop_pct = (price - support) / price * 100 if price > 0 else 0.0

    # Pisos y techos de cordura, en unidades de volatilidad propia: un
    # objetivo a menos de 1 movimiento tipico es ruido, y un stop a mas de
    # 3 es tan holgado que deja de proteger.
    if target_pct < typical:
        target_pct = typical * 4.0  # ya en maximos: se proyecta extension
    if not (typical * 0.8 <= stop_pct <= typical * 3.0):
        stop_pct = typical * 1.5

    return target_pct, stop_pct


def compute(
    closes: list[float],
    highs: list[float] | None = None,
    lows: list[float] | None = None,
    horizon: int = 10,
    structure_window: int = 20,
    volatility_window: int = 20,
) -> Asymmetry | None:
    """None si no hay historia suficiente para medir nada -- eso es un
    resultado legitimo, no un error. Un fallo real (datos corruptos,
    tipos invalidos) si levanta excepcion.
    """
    if not closes or len(closes) < volatility_window + horizon + 2:
        return None
    if any(c < 0 for c in closes):
        raise ValueError("Serie de precios con valores negativos")

    typical = _typical_move_pct(closes, highs, lows, volatility_window)
    if typical is None or typical <= 0:
        return None

    target_pct, stop_pct = _structural_levels(closes, highs, lows, structure_window, typical)
    if target_pct <= 0 or stop_pct <= 0:
        return None

    wins, losses, timeouts = _barrier_outcomes(closes, target_pct, stop_pct, horizon)
    total = wins + losses + timeouts
    if total == 0:
        return None

    resolved = wins + losses
    # Las expiradas se cuentan en el denominador de la esperanza (aportan
    # ~0: se sale a mercado sin ganancia ni perdida relevante) pero se
    # excluyen de win_prob, que responde "cuando se define, cuantas veces
    # sale bien". Mezclarlas ahi haria ver como perdedor a un activo que
    # simplemente se mueve despacio.
    win_prob = wins / resolved if resolved else 0.0
    expected_value = (wins * target_pct - losses * stop_pct) / total

    return Asymmetry(
        target_pct=round(target_pct, 2),
        stop_pct=round(stop_pct, 2),
        reward_risk=round(target_pct / stop_pct, 2),
        win_prob=round(win_prob, 3),
        expected_value_pct=round(expected_value, 3),
        volatility_pct=round(typical, 2),
        sample_size=total,
        resolved_pct=round(resolved / total * 100, 1),
    )
