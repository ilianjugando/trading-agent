import pytest

from execution.sizing import BUCKET_CAPS, MIN_ORDER_USD, PositionSize, classify_bucket, size_position


def test_moonshot_stays_tiny_even_at_full_conviction():
    """La razon de ser del modulo: una apuesta especulativa tiene que poder
    ser chica. Antes todo trade era el 20% del pool."""
    result = size_position(pool_value=1_000_000, bucket="moonshot", conviction=1.0, max_trade_pct=0.20)
    assert result.viable
    assert result.usd == 5000.0  # 0.5% de 1M, no 200k
    assert result.pct_of_pool == 0.5


def test_size_scales_linearly_with_conviction():
    """Media conviccion entra con la mitad del capital, en vez de no entrar."""
    full = size_position(1_000_000, "momentum", conviction=1.0, max_trade_pct=0.20)
    half = size_position(1_000_000, "momentum", conviction=0.5, max_trade_pct=0.20)
    assert half.usd == pytest.approx(full.usd / 2)
    assert half.viable


def test_never_exceeds_the_spend_guard_ceiling():
    """max_trade_pct es el techo duro del SpendGuard: si es mas restrictivo
    que el bucket, manda el mas chico."""
    result = size_position(1_000_000, "core", conviction=1.0, max_trade_pct=0.02)
    assert result.pct_of_pool == 2.0
    assert result.usd == 20_000.0


def test_every_bucket_cap_is_below_the_old_fixed_size():
    """Ningun tamano nuevo puede ser mayor al 20% que el sistema usaba
    siempre -- esto no afloja riesgo, lo reduce."""
    for cap in BUCKET_CAPS.values():
        assert cap <= 0.20


def test_negative_expectancy_sizes_to_zero():
    result = size_position(1_000_000, "momentum", conviction=0.9,
                           max_trade_pct=0.20, expected_value_pct=-1.5)
    assert not result.viable
    assert result.usd == 0.0
    assert "esperanza negativa" in result.reason


def test_below_minimum_order_is_not_viable():
    result = size_position(pool_value=100, bucket="moonshot", conviction=0.1, max_trade_pct=0.20)
    assert not result.viable
    assert str(int(MIN_ORDER_USD)) in result.reason


def test_rejects_unknown_bucket_and_bad_pool_value():
    with pytest.raises(ValueError):
        size_position(1000, "no-existe", 0.5, 0.20)
    with pytest.raises(ValueError):
        size_position(0, "momentum", 0.5, 0.20)


def test_conviction_is_clamped():
    over = size_position(1_000_000, "momentum", conviction=5.0, max_trade_pct=0.20)
    at_one = size_position(1_000_000, "momentum", conviction=1.0, max_trade_pct=0.20)
    assert over.usd == at_one.usd

    under = size_position(1_000_000, "momentum", conviction=-2.0, max_trade_pct=0.20)
    assert under.usd == 0.0


def test_micro_cap_always_lands_in_moonshot():
    """El riesgo de una micro cap es no poder salir, y eso no lo arregla
    tener razon sobre la direccion."""
    assert classify_bucket("micro", reward_risk=10.0) == "moonshot"
    assert classify_bucket("micro", reward_risk=None) == "moonshot"


def test_bucket_classification_by_size_and_asymmetry():
    assert classify_bucket("large", reward_risk=1.5) == "core"
    assert classify_bucket("small", reward_risk=5.0) == "moonshot"
    assert classify_bucket("mid", reward_risk=2.0) == "momentum"
    # Sin dato de capitalizacion se asume el caso intermedio, no el mejor.
    assert classify_bucket(None, reward_risk=2.0) == "momentum"


def test_meme_coin_always_lands_in_moonshot_regardless_of_market_cap():
    """Evidencia (SSRN 6292920): meme coins grandes por market cap
    ("large") igual perdieron -78.74% de cartera equal-weighted en 14
    meses. La capitalizacion no protege de un colapso narrativo, asi que
    is_meme debe ganarle incluso a "large" con buena asimetria."""
    assert classify_bucket("large", reward_risk=1.5, is_meme=True) == "moonshot"
    assert classify_bucket("mid", reward_risk=2.0, is_meme=True) == "moonshot"
    assert classify_bucket(None, reward_risk=2.0, is_meme=True) == "moonshot"
    # Por defecto (is_meme no pasado) el comportamiento previo no cambia.
    assert classify_bucket("large", reward_risk=1.5) == "core"


def test_result_is_json_serializable():
    import json
    from dataclasses import asdict
    result = size_position(1_000_000, "momentum", 0.7, 0.20)
    assert isinstance(result, PositionSize)
    json.dumps(asdict(result))
