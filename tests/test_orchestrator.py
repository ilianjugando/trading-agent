import json
from datetime import datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from execution.orchestrator import (
    _check_deployment_alert,
    _evaluate_candidates,
    _manage_crypto_exits,
    _market_is_open,
)
from execution.positions import PositionTracker
from risk.circuit_breaker import CircuitBreaker
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


class _FakeOKXExit:
    def __init__(self, price, balances):
        self.price = price
        self.balances = balances
        self.sell_calls = []

    def get_last_price(self, inst_id):
        return self.price

    def get_balance(self, ccy):
        return self.balances.get(ccy, 0.0)

    def place_market_order(self, inst_id, sz, side):
        self.sell_calls.append((inst_id, sz))
        return {"instId": inst_id, "side": side, "ordId": "1", "status": "submitted"}


def _breaker(tmp_path):
    return CircuitBreaker("crypto", tmp_path, daily_loss_halt_pct=0.5, max_consecutive_losses=10)


def test_manage_crypto_exits_sells_the_real_balance_not_the_stale_tracked_qty(tmp_path):
    positions = PositionTracker("crypto", tmp_path)
    positions.open("ARB-USDT", entry_price=0.15, qty=1000.0, stop=0.14)
    okx = _FakeOKXExit(0.10, {"ARB": 800.0})

    _manage_crypto_exits(_settings(tmp_path), okx, positions, _breaker(tmp_path), pool_value=1000.0)

    assert okx.sell_calls == [("ARB-USDT", 800.0)]
    assert positions.get("ARB-USDT") is None
    decisions = _decisions(tmp_path)
    assert decisions[-1]["result"] == "stopped_out"


def test_manage_crypto_exits_clears_phantom_position_without_attempting_a_sell(tmp_path):
    positions = PositionTracker("crypto", tmp_path)
    positions.open("IOST-USDT", entry_price=0.0019, qty=800000.0, stop=0.0017)
    okx = _FakeOKXExit(0.001, {})

    _manage_crypto_exits(_settings(tmp_path), okx, positions, _breaker(tmp_path), pool_value=1000.0)

    assert okx.sell_calls == []
    assert positions.get("IOST-USDT") is None
    decisions = _decisions(tmp_path)
    assert decisions[-1]["result"] == "phantom_position_cleared"
    assert not (tmp_path / "trades.log").exists()


def test_manage_crypto_exits_leaves_position_open_when_price_above_stop(tmp_path):
    positions = PositionTracker("crypto", tmp_path)
    positions.open("ARB-USDT", entry_price=0.15, qty=1000.0, stop=0.14)
    okx = _FakeOKXExit(0.20, {"ARB": 1000.0})

    _manage_crypto_exits(_settings(tmp_path), okx, positions, _breaker(tmp_path), pool_value=1000.0)

    assert okx.sell_calls == []
    assert positions.get("ARB-USDT") is not None


def _crypto_settings(tmp_path):
    return SimpleNamespace(
        okx_api_key="k", okx_api_secret="s", okx_api_passphrase="p", okx_demo_flag="1",
        state_dir=tmp_path, logs_dir=tmp_path,
        max_trade_pct=0.20, daily_loss_halt_pct=0.10, max_consecutive_losses=3,
        crypto_universe_size=5, enable_kronos_forecast=False,
        gemini_api_key="", nvidia_api_key="",
    )


def test_halted_breaker_still_runs_exits_but_blocks_new_entries(tmp_path, monkeypatch):
    """P0 (auditoria 2026-09-10): breaker.check() corria ANTES de gestionar
    salidas. Al dispararse el corte -- es decir, justo cuando el mercado va
    en contra -- levantaba TradingHalted y el stop-loss de cada posicion
    abierta dejaba de ejecutarse. El corte tiene que frenar riesgo NUEVO,
    nunca frenar la REDUCCION de riesgo ya tomado.

    En crypto era peor: run_crypto ni siquiera capturaba TradingHalted, asi
    que se reportaba como 'error' con traceback en vez de 'halted'."""
    import execution.orchestrator as orch
    from risk.circuit_breaker import TradingHalted

    calls = []

    class _FakeOKX:
        def __init__(self, *a, **kw):
            pass

        def get_total_equity_usd(self):
            return 100_000.0

    class _HaltedBreaker:
        def __init__(self, *a, **kw):
            pass

        def check(self, pool_value):
            raise TradingHalted("[crypto] Halted: drawdown de prueba")

    def _fake_exits(*a, **kw):
        calls.append("exits")

    def _explode(*a, **kw):
        raise AssertionError("no se puede buscar candidatos nuevos con el bot detenido")

    monkeypatch.setattr(orch, "OKXAdapter", _FakeOKX)
    monkeypatch.setattr(orch, "CircuitBreaker", _HaltedBreaker)
    monkeypatch.setattr(orch, "_manage_crypto_exits", _fake_exits)
    monkeypatch.setattr(orch, "liquid_crypto_universe", _explode)

    orch.run_crypto(_crypto_settings(tmp_path))

    assert "exits" in calls, "las salidas TIENEN que correr aunque el bot este detenido"
    results = [d.get("result") for d in _decisions(tmp_path)]
    assert "halted" in results, "un corte deliberado se reporta como 'halted', no como crash"
