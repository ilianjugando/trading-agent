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
