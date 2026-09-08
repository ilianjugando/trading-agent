from dataclasses import dataclass

import pytest

from scripts.smoke_test_order import refuse_unless_safe


@dataclass
class _FakeSettings:
    mode: str
    okx_demo_flag: str
    ibkr_port: int = 4002


def test_refuses_when_mode_is_live():
    settings = _FakeSettings(mode="live", okx_demo_flag="1")
    with pytest.raises(SystemExit):
        refuse_unless_safe(settings, broker="okx", confirm=True)


def test_refuses_okx_when_not_demo_flag():
    settings = _FakeSettings(mode="paper", okx_demo_flag="0")
    with pytest.raises(SystemExit):
        refuse_unless_safe(settings, broker="okx", confirm=True)


def test_refuses_without_confirm_flag():
    settings = _FakeSettings(mode="paper", okx_demo_flag="1")
    with pytest.raises(SystemExit) as exc_info:
        refuse_unless_safe(settings, broker="okx", confirm=False)
    assert exc_info.value.code == 0  # dry-run exit, not an error


def test_allows_paper_okx_demo_with_confirm():
    settings = _FakeSettings(mode="paper", okx_demo_flag="1")
    refuse_unless_safe(settings, broker="okx", confirm=True)  # should not raise


def test_allows_paper_ibkr_with_confirm():
    settings = _FakeSettings(mode="paper", okx_demo_flag="0")  # irrelevant for ibkr
    refuse_unless_safe(settings, broker="ibkr", confirm=True)  # should not raise
