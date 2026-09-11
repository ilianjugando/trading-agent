import math

import pytest

from signals.custom import Rule, StrategySpec, delete, load_all, save


def _rising(n=120):
    return [100 * (1.004 ** i) + 9 * math.sin(i / 3.0) for i in range(n)]


def test_a_spec_becomes_a_normal_strategy_function():
    """El punto de todo esto: una estrategia custom tiene que ser
    indistinguible de las de signals/strategies.py para el backtest, el
    torneo y el bot en vivo."""
    spec = StrategySpec(name="test", entry=[Rule("sma_trend", "==", "up")])
    fn = spec.as_fn()
    p = fn(_rising())
    assert p is not None
    assert p.strategy == "test"
    assert p.entry_price == _rising()[-1]


def test_all_conditions_must_hold():
    closes = _rising()
    spec = StrategySpec(name="t", entry=[
        Rule("sma_trend", "==", "up"),
        Rule("rsi", "<", 5),  # imposible aca
    ])
    assert spec.as_fn()(closes) is None


def test_missing_indicator_data_does_not_trade():
    """Sin dato no se opera. Un indicador que devuelve None sobre una
    ventana corta no puede leerse como condicion cumplida."""
    spec = StrategySpec(name="t", entry=[Rule("above_high_20", "==", True)])
    assert spec.as_fn()([100.0, 101.0]) is None


def test_roundtrip_through_disk(tmp_path):
    spec = StrategySpec(name="mi_estrategia", entry=[Rule("rsi", "<", 35)], description="compra caidas")
    save(tmp_path, spec)

    loaded = load_all(tmp_path)["mi_estrategia"]
    assert loaded.description == "compra caidas"
    assert loaded.entry[0].indicator == "rsi"
    assert loaded.as_fn() is not None  # sigue siendo ejecutable tras el roundtrip


def test_rejects_a_strategy_with_no_entry_conditions(tmp_path):
    """Sin condiciones compraria en cada barra."""
    with pytest.raises(ValueError, match="sin condiciones"):
        save(tmp_path, StrategySpec(name="vacia", entry=[]))


def test_rejects_unknown_indicator_and_operator(tmp_path):
    with pytest.raises(ValueError, match="indicador desconocido"):
        save(tmp_path, StrategySpec(name="x", entry=[Rule("no_existe", "<", 1)]))
    with pytest.raises(ValueError, match="operador desconocido"):
        save(tmp_path, StrategySpec(name="x", entry=[Rule("rsi", "~~", 1)]))


def test_rejects_a_name_that_is_not_a_plain_identifier(tmp_path):
    """El nombre termina en un archivo y en la UI -- nada de rutas."""
    with pytest.raises(ValueError):
        save(tmp_path, StrategySpec(name="../../etc/passwd", entry=[Rule("rsi", "<", 30)]))


def test_delete(tmp_path):
    save(tmp_path, StrategySpec(name="temporal", entry=[Rule("rsi", "<", 30)]))
    assert delete(tmp_path, "temporal") is True
    assert delete(tmp_path, "temporal") is False
    assert load_all(tmp_path) == {}


def test_corrupt_file_does_not_crash_the_dashboard(tmp_path):
    (tmp_path / "custom_strategies.json").write_text("no es json")
    assert load_all(tmp_path) == {}


def test_a_custom_spec_can_reproduce_an_existing_strategy():
    """mean_reversion es 'rsi < 30'. Si esto no lo reproduce, el formato
    no alcanza para las estrategias que el proyecto ya opera."""
    crash = [100 - i * 0.9 for i in range(80)]
    spec = StrategySpec(name="reversion_custom", entry=[Rule("rsi", "<", 30)])

    from signals.strategies import mean_reversion

    assert (spec.as_fn()(crash) is None) == (mean_reversion(crash) is None)
