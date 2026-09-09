import pytest

from signals.asymmetry import compute


def _series(n: int, start: float = 100.0, step: float = 0.5) -> list[float]:
    return [start + i * step for i in range(n)]


def test_returns_none_on_short_history():
    assert compute(_series(10)) is None


def test_returns_none_on_empty():
    assert compute([]) is None


def test_raises_on_negative_prices():
    bad = _series(60)
    bad[10] = -5.0
    with pytest.raises(ValueError):
        compute(bad)


def test_flat_series_has_no_measurable_volatility():
    # Sin movimiento no hay nada que medir -- resultado legitimo, no error.
    assert compute([100.0] * 60) is None


def test_steady_uptrend_scores_positive_expectancy():
    result = compute(_series(120))
    assert result is not None
    # Una serie que solo sube toca el objetivo antes que el stop siempre.
    assert result.win_prob > 0.9
    assert result.expected_value_pct > 0
    assert result.sample_size > 0


def test_steady_downtrend_scores_negative_expectancy():
    result = compute(_series(120, start=200.0, step=-0.5))
    assert result is not None
    assert result.win_prob < 0.1
    assert result.expected_value_pct < 0


def test_reward_risk_varies_with_price_structure():
    """Lo que motivo usar niveles estructurales: con multiplos fijos de
    volatilidad el R:R salia constante para todos los activos y no servia
    para comparar candidatos."""
    # Subida y despues caida: el precio queda muy por debajo del maximo
    # reciente, o sea con mucho recorrido libre hasta la resistencia.
    pullback = [100.0 + i for i in range(60)] + [160.0 - i * 1.3 for i in range(40)]
    # Serie que solo sube: el precio ESTA en su maximo, sin techo cercano.
    at_the_high = _series(120, step=1.0)

    a = compute(pullback)
    b = compute(at_the_high)
    assert a is not None and b is not None
    # Estructuras opuestas deben producir asimetrias distintas; con la
    # version anterior (multiplos fijos) ambas daban exactamente 2.67.
    assert a.target_pct != b.target_pct


def test_all_fields_are_native_python_types():
    """El resultado va directo a json.dumps() para el prompt del panel;
    un escalar de numpy ya rompio ese camino antes."""
    result = compute(_series(120))
    assert result is not None
    for value in (result.target_pct, result.stop_pct, result.reward_risk,
                  result.win_prob, result.expected_value_pct, result.volatility_pct,
                  result.resolved_pct):
        assert type(value) is float
    assert type(result.sample_size) is int


def test_highs_and_lows_widen_the_volatility_estimate():
    """El rango intradia captura mechas que los cierres esconden; ignorarlo
    subestima el riesgo real de que se toque un stop."""
    closes = _series(120)
    highs = [c * 1.05 for c in closes]
    lows = [c * 0.95 for c in closes]

    with_ohlc = compute(closes, highs=highs, lows=lows)
    closes_only = compute(closes)
    assert with_ohlc is not None and closes_only is not None
    assert with_ohlc.volatility_pct > closes_only.volatility_pct
