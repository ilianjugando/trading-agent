import json

import pytest

from risk.circuit_breaker import CircuitBreaker, TradingHalted


def test_no_halt_when_within_limits(tmp_path):
    breaker = CircuitBreaker("test", tmp_path, daily_loss_halt_pct=0.10, max_consecutive_losses=3)
    breaker.check(pool_value=100)  # establishes day_start_value=100
    breaker.check(pool_value=95)  # -5%, within -10% limit


def test_halts_on_daily_drawdown(tmp_path):
    breaker = CircuitBreaker("test", tmp_path, daily_loss_halt_pct=0.10, max_consecutive_losses=3)
    breaker.check(pool_value=100)  # day_start_value=100
    with pytest.raises(TradingHalted):
        breaker.check(pool_value=88)  # -12%, breaches -10% limit


def test_halt_persists_across_instances(tmp_path):
    breaker = CircuitBreaker("test", tmp_path, daily_loss_halt_pct=0.10, max_consecutive_losses=3)
    breaker.check(pool_value=100)
    with pytest.raises(TradingHalted):
        breaker.check(pool_value=88)

    # Simulate a process restart: new instance, same state dir.
    breaker2 = CircuitBreaker("test", tmp_path, daily_loss_halt_pct=0.10, max_consecutive_losses=3)
    with pytest.raises(TradingHalted):
        breaker2.check(pool_value=88)


def test_halts_on_consecutive_losses(tmp_path):
    breaker = CircuitBreaker("test", tmp_path, daily_loss_halt_pct=0.10, max_consecutive_losses=3)
    breaker.check(pool_value=100)
    breaker.record_trade_result(won=False)
    breaker.record_trade_result(won=False)
    breaker.record_trade_result(won=False)
    with pytest.raises(TradingHalted):
        breaker.check(pool_value=100)


def test_win_resets_consecutive_losses(tmp_path):
    breaker = CircuitBreaker("test", tmp_path, daily_loss_halt_pct=0.10, max_consecutive_losses=3)
    breaker.check(pool_value=100)
    breaker.record_trade_result(won=False)
    breaker.record_trade_result(won=False)
    breaker.record_trade_result(won=True)
    breaker.check(pool_value=100)  # should not raise


def test_manual_reset_clears_halt(tmp_path):
    breaker = CircuitBreaker("test", tmp_path, daily_loss_halt_pct=0.10, max_consecutive_losses=3)
    breaker.check(pool_value=100)
    with pytest.raises(TradingHalted):
        breaker.check(pool_value=88)

    breaker.reset()
    breaker.check(pool_value=88)  # fresh state, no longer halted


def test_immaterial_losses_do_not_count_toward_the_streak(tmp_path):
    """Una cartera de apuestas asimetricas tiene rachas largas de perdidas
    chicas por diseno. Con el contador viejo (contaba eventos, no dano) se
    auto-detenia el primer dia sin haber perdido casi nada."""
    breaker = CircuitBreaker("test", tmp_path, daily_loss_halt_pct=0.10, max_consecutive_losses=3)
    for _ in range(10):
        breaker.record_trade_result(won=False, loss_pct_of_pool=0.003)  # 0,3% del pool

    state = json.loads((tmp_path / "breaker_test.json").read_text())
    assert state["consecutive_losses"] == 0
    assert not state["halted"]


def test_material_losses_still_halt(tmp_path):
    """La proteccion real no se toco: perdidas grandes siguen deteniendo."""
    breaker = CircuitBreaker("test", tmp_path, daily_loss_halt_pct=0.10, max_consecutive_losses=3)
    for _ in range(3):
        breaker.record_trade_result(won=False, loss_pct_of_pool=0.08)  # 8% del pool

    state = json.loads((tmp_path / "breaker_test.json").read_text())
    assert state["consecutive_losses"] == 3
    assert state["halted"]


def test_unknown_loss_size_is_treated_as_material(tmp_path):
    """Sin dato del tamano se cuenta como material: el lado conservador."""
    breaker = CircuitBreaker("test", tmp_path, daily_loss_halt_pct=0.10, max_consecutive_losses=3)
    for _ in range(3):
        breaker.record_trade_result(won=False)

    assert json.loads((tmp_path / "breaker_test.json").read_text())["halted"]


def test_a_win_still_resets_the_streak(tmp_path):
    breaker = CircuitBreaker("test", tmp_path, daily_loss_halt_pct=0.10, max_consecutive_losses=3)
    breaker.record_trade_result(won=False, loss_pct_of_pool=0.08)
    breaker.record_trade_result(won=False, loss_pct_of_pool=0.08)
    breaker.record_trade_result(won=True)

    state = json.loads((tmp_path / "breaker_test.json").read_text())
    assert state["consecutive_losses"] == 0
    assert not state["halted"]
