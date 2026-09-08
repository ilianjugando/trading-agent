from datetime import datetime
from zoneinfo import ZoneInfo

from execution.orchestrator import _market_is_open

_NY = ZoneInfo("America/New_York")


def test_open_during_regular_hours():
    assert _market_is_open(datetime(2026, 9, 8, 10, 0, tzinfo=_NY))  # Tuesday


def test_closed_before_open():
    assert not _market_is_open(datetime(2026, 9, 8, 9, 0, tzinfo=_NY))


def test_closed_after_close():
    assert not _market_is_open(datetime(2026, 9, 8, 16, 30, tzinfo=_NY))


def test_closed_on_weekend():
    assert not _market_is_open(datetime(2026, 9, 5, 12, 0, tzinfo=_NY))  # Saturday


def test_open_boundary_930_inclusive():
    assert _market_is_open(datetime(2026, 9, 8, 9, 30, tzinfo=_NY))


def test_closed_boundary_1600_exclusive():
    assert not _market_is_open(datetime(2026, 9, 8, 16, 0, tzinfo=_NY))
