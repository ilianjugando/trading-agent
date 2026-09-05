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
