from datetime import datetime, timedelta, timezone

import pytest

from execution.tournament import record, record_universe, score_due, standings
from signals.strategies import Proposal, breakout, evaluate_all, mean_reversion, momentum, trend_follow

NOW = datetime(2026, 9, 8, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
def db(tmp_path):
    return tmp_path / "tournament.db"


# --- strategies ---------------------------------------------------------

def test_momentum_fires_on_a_real_move():
    assert momentum([100.0, 106.0]) is not None


def test_momentum_ignores_noise():
    assert momentum([100.0, 100.4]) is None


def test_mean_reversion_fires_when_oversold():
    falling = [100.0 - i * 2 for i in range(30)]
    assert mean_reversion(falling) is not None


def test_mean_reversion_silent_when_not_oversold():
    rising = [100.0 + i for i in range(30)]
    assert mean_reversion(rising) is None


def test_trend_follow_needs_an_uptrend():
    assert trend_follow([100.0 + i for i in range(40)]) is not None
    assert trend_follow([100.0 - i for i in range(40)]) is None


def test_breakout_needs_a_new_high():
    flat = [100.0] * 25
    assert breakout(flat) is None
    assert breakout(flat + [120.0]) is not None


def test_evaluate_all_returns_only_strategies_that_fired():
    flat = [100.0] * 40
    names = {p.strategy for p in evaluate_all(flat)}
    assert "momentum" not in names  # nothing moved


# --- recording and scoring ----------------------------------------------

def test_record_writes_one_row_per_proposal(db):
    written = record(db, "crypto", "BTC-USDT", [
        Proposal("momentum", "subio", 100.0),
        Proposal("breakout", "rompio", 100.0),
    ], now=NOW)
    assert written == 2
    assert sum(s.open_proposals for s in standings(db)) == 2


def test_record_universe_covers_every_symbol(db):
    written = record_universe(db, "crypto", {
        "AAA-USDT": [100.0] * 25 + [130.0],   # breakout + momentum
        "BBB-USDT": [100.0] * 26,             # nothing fires
    }, now=NOW)
    assert written >= 1
    assert {s.strategy for s in standings(db)}


def test_score_due_closes_old_proposals_at_current_price(db):
    record(db, "crypto", "BTC-USDT", [Proposal("momentum", "subio", 100.0)], now=NOW)
    later = NOW + timedelta(hours=25)

    assert score_due(db, lambda _s: 110.0, now=later) == 1

    row = next(s for s in standings(db) if s.strategy == "momentum")
    assert row.scored == 1
    assert row.avg_return_pct == pytest.approx(10.0)
    assert row.win_rate == 100.0


def test_score_due_leaves_fresh_proposals_alone(db):
    record(db, "crypto", "BTC-USDT", [Proposal("momentum", "subio", 100.0)], now=NOW)
    soon = NOW + timedelta(hours=2)

    assert score_due(db, lambda _s: 110.0, now=soon) == 0
    assert next(s for s in standings(db)).open_proposals == 1


def test_score_due_retries_instead_of_scoring_on_a_failed_lookup(db):
    record(db, "crypto", "BTC-USDT", [Proposal("momentum", "subio", 100.0)], now=NOW)
    later = NOW + timedelta(hours=25)

    def broken(_symbol):
        raise RuntimeError("exchange down")

    assert score_due(db, broken, now=later) == 0
    # Still open, so the next run can score it properly rather than
    # locking in a wrong price.
    assert next(s for s in standings(db)).open_proposals == 1


def test_standings_ranks_by_average_return(db):
    record(db, "crypto", "AAA", [Proposal("winner", "x", 100.0)], now=NOW)
    record(db, "crypto", "BBB", [Proposal("loser", "x", 100.0)], now=NOW)
    later = NOW + timedelta(hours=25)
    score_due(db, lambda s: 120.0 if s == "AAA" else 90.0, now=later)

    ranked = standings(db)
    assert [s.strategy for s in ranked] == ["winner", "loser"]
    assert ranked[0].avg_return_pct == pytest.approx(20.0)
    assert ranked[1].avg_return_pct == pytest.approx(-10.0)


def test_standings_empty_on_a_fresh_database(db):
    assert standings(db) == []
