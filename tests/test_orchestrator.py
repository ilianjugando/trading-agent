import json
from datetime import datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from execution.orchestrator import (
    _check_deployment_alert,
    _evaluate_candidates,
    _market_is_open,
)
from signals.opportunity_scanner import Candidate, ScanResult

_NY = ZoneInfo("America/New_York")


def _settings(tmp_path):
    return SimpleNamespace(logs_dir=tmp_path)


def _candidate(symbol: str, score: float = 90.0) -> Candidate:
    return Candidate(symbol=symbol, score=score, asymmetry=None)


def _decisions(tmp_path) -> list[dict]:
    path = tmp_path / "decisions.log"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines()]


class _NoPositions:
    """Fake PositionTracker: reports zero open positions regardless of
    `held`, since these tests only exercise the already-held short-circuit
    in _evaluate_candidates, which returns before touching positions."""

    def all_open(self):
        return {}


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


def test_evaluate_candidates_logs_visibly_when_already_held(tmp_path):
    """Bug real (2026-09-10): un candidato ya en cartera se saltaba con un
    `continue` silencioso, indistinguible en el log de 'no se evaluo nada'."""
    scan_result = ScanResult(scanned=1, shortlist=[_candidate("NEAR-USDT")],
                             rejected=[], rejection_summary={})
    executed = _evaluate_candidates(
        _settings(tmp_path), scan_result, pool="crypto", pool_value=1000.0,
        held={"NEAR-USDT"}, guard=None, positions=_NoPositions(),
        place_order=lambda *a: (_ for _ in ()).throw(AssertionError("no debia comprar")),
    )
    assert executed == 0
    decisions = _decisions(tmp_path)
    assert len(decisions) == 1
    assert decisions[0]["result"] == "skipped_already_held"
    assert decisions[0]["symbol"] == "NEAR-USDT"


def test_deployment_alert_recognizes_top_candidates_already_held(tmp_path):
    """La alerta no debe decir SYSTEM_FAILED_TO_DEPLOY cuando la razon real
    es que los mejores candidatos ya son posiciones abiertas -- eso no es
    una falla, es el sistema evitando comprar lo que ya tiene."""
    top = [_candidate("NEAR-USDT"), _candidate("ARB-USDT")]
    scan_result = ScanResult(scanned=50, shortlist=top, rejected=[], rejection_summary={})
    _check_deployment_alert(_settings(tmp_path), "crypto", scan_result, executed=0,
                            pool_value=1000.0, held={"NEAR-USDT", "ARB-USDT"})
    alert = _decisions(tmp_path)[0]
    assert alert["diagnosis"] == "TOP_CANDIDATES_ALREADY_HELD"


def test_deployment_alert_still_flags_a_real_failure(tmp_path):
    """Si el mejor candidato NO esta en cartera y aun asi no se ejecuto
    nada, sigue siendo una falla real que hay que investigar."""
    top = [_candidate("NEAR-USDT")]
    scan_result = ScanResult(scanned=50, shortlist=top, rejected=[], rejection_summary={})
    _check_deployment_alert(_settings(tmp_path), "crypto", scan_result, executed=0,
                            pool_value=1000.0, held=set())
    alert = _decisions(tmp_path)[0]
    assert alert["diagnosis"] == "SYSTEM_FAILED_TO_DEPLOY"


def test_deployment_alert_no_opportunities(tmp_path):
    scan_result = ScanResult(scanned=50, shortlist=[], rejected=[], rejection_summary={})
    _check_deployment_alert(_settings(tmp_path), "crypto", scan_result, executed=0,
                            pool_value=1000.0, held=set())
    alert = _decisions(tmp_path)[0]
    assert alert["diagnosis"] == "NO_OPPORTUNITIES_FOUND"


def test_deployment_alert_skipped_when_something_executed(tmp_path):
    scan_result = ScanResult(scanned=50, shortlist=[_candidate("NEAR-USDT")],
                             rejected=[], rejection_summary={})
    _check_deployment_alert(_settings(tmp_path), "crypto", scan_result, executed=1,
                            pool_value=1000.0, held=set())
    assert _decisions(tmp_path) == []
