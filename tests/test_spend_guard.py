import pytest

from risk.spend_guard import SpendGuard, SpendLimitError


def test_trade_under_cap_passes(tmp_path):
    guard = SpendGuard("test", tmp_path, max_trade_pct=0.20, daily_loss_halt_pct=0.10)
    guard.check_and_record(usd_amount=15, pool_value=100)  # 15% of pool


def test_trade_over_per_trade_cap_rejected(tmp_path):
    guard = SpendGuard("test", tmp_path, max_trade_pct=0.20, daily_loss_halt_pct=0.10)
    with pytest.raises(SpendLimitError):
        guard.check_and_record(usd_amount=25, pool_value=100)  # 25% > 20% cap


def test_cumulative_daily_cap_rejected(tmp_path):
    guard = SpendGuard("test", tmp_path, max_trade_pct=0.20, daily_loss_halt_pct=0.10)
    # daily turnover cap = pool_value * daily_loss_halt_pct * 5 = 100 * 0.10 * 5 = 50
    guard.check_and_record(usd_amount=20, pool_value=100)
    guard.check_and_record(usd_amount=20, pool_value=100)
    with pytest.raises(SpendLimitError):
        guard.check_and_record(usd_amount=20, pool_value=100)  # total would be 60 > 50


def test_zero_or_negative_amount_rejected(tmp_path):
    guard = SpendGuard("test", tmp_path, max_trade_pct=0.20, daily_loss_halt_pct=0.10)
    with pytest.raises(SpendLimitError):
        guard.check_and_record(usd_amount=0, pool_value=100)


def test_day_boundary_is_utc_not_the_machines_local_date(tmp_path):
    """Misma clase de bug que en circuit_breaker: el limite diario de gasto
    usaba date.today() (fecha LOCAL). Todos los timestamps del sistema son
    UTC, asi que el 'dia' del presupuesto no era el mismo 'dia' del log."""
    import json

    import risk.spend_guard as sg

    guard = sg.SpendGuard("test", tmp_path, max_trade_pct=0.20, daily_loss_halt_pct=0.10)
    guard.check_and_record(100.0, pool_value=10_000.0)

    state = json.loads((tmp_path / "spend_test.json").read_text())
    assert state["date"] == sg._utc_today()


def test_refund_devuelve_el_presupuesto_de_una_orden_que_no_se_ejecuto(tmp_path):
    """check_and_record reserva ANTES de mandar la orden, asi que sin
    devolucion cada rechazo del broker quemaba rotacion diaria que nunca se
    uso: suficientes rechazos y el bot se quedaba sin presupuesto por el
    resto del dia sin haber operado nada.

    Con pool 1000: tope por operacion 200 (20%), tope diario de rotacion 500
    (0.10 * 5). Tres reservas de 200 se pasan del diario; si la del medio se
    devuelve, la tercera entra."""
    guard = SpendGuard("stocks", tmp_path, max_trade_pct=0.20, daily_loss_halt_pct=0.10)

    guard.check_and_record(200.0, pool_value=1000.0)   # gastado 200
    guard.check_and_record(200.0, pool_value=1000.0)   # gastado 400
    guard.refund(200.0)                                # la orden se rechazo -> 200

    guard.check_and_record(200.0, pool_value=1000.0)   # 400: sin devolucion serian 600 > 500


def test_refund_no_deja_el_gasto_en_negativo(tmp_path):
    """Si el archivo se reinicio por cambio de fecha entre la reserva y la
    devolucion, una resta pelada dejaria el gasto del dia en negativo, o sea
    presupuesto diario regalado."""
    guard = SpendGuard("stocks", tmp_path, max_trade_pct=0.20, daily_loss_halt_pct=0.10)

    guard.refund(250.0)                                # devolucion sin reserva previa
    guard.check_and_record(200.0, pool_value=1000.0)
    guard.check_and_record(200.0, pool_value=1000.0)   # gastado 400 de 500

    try:
        guard.check_and_record(200.0, pool_value=1000.0)
    except SpendLimitError:
        return
    raise AssertionError("la devolucion regalo presupuesto: 600 > tope diario de 500")
