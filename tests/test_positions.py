from execution.positions import PositionTracker


def test_open_and_get(tmp_path):
    tracker = PositionTracker("test", tmp_path)
    tracker.open("NVDA", entry_price=100, qty=1, stop=95)
    pos = tracker.get("NVDA")
    assert pos == {"entry_price": 100, "qty": 1, "stop": 95}


def test_stop_only_trails_up(tmp_path):
    tracker = PositionTracker("test", tmp_path)
    tracker.open("NVDA", entry_price=100, qty=1, stop=95)

    tracker.update_stop("NVDA", 98)
    assert tracker.get("NVDA")["stop"] == 98

    tracker.update_stop("NVDA", 90)  # lower than current stop -- must be ignored
    assert tracker.get("NVDA")["stop"] == 98


def test_close_removes_position(tmp_path):
    tracker = PositionTracker("test", tmp_path)
    tracker.open("NVDA", entry_price=100, qty=1, stop=95)
    closed = tracker.close("NVDA")
    assert closed["entry_price"] == 100
    assert tracker.get("NVDA") is None


def test_persists_across_instances(tmp_path):
    PositionTracker("test", tmp_path).open("NVDA", entry_price=100, qty=1, stop=95)
    tracker2 = PositionTracker("test", tmp_path)
    assert tracker2.get("NVDA") is not None
