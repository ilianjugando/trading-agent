"""Tests portados de tests/test_dashboard.py -- misma logica (api/data.py,
api/bot_control.py son movidas tal cual del dashboard.py anterior), solo
retargeteados al nuevo paquete. Mas los tests nuevos de contrato HTTP que
antes no existian (via TestClient de FastAPI, sin levantar un server
real)."""
import json

from fastapi.testclient import TestClient

from api import bot_control, data
from signals import market_radar
from api.app import app


def _write_jsonl(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n" if rows else "")


def test_portfolio_series_requires_both_pools_before_emitting_a_point(tmp_path, monkeypatch):
    monkeypatch.setattr(data, "LOGS_DIR", tmp_path)
    _write_jsonl(tmp_path / "portfolio_history.jsonl", [
        {"timestamp": "2026-01-01T00:00:00+00:00", "pool": "crypto", "total_value": 100.0},
        {"timestamp": "2026-01-01T00:05:00+00:00", "pool": "crypto", "total_value": 110.0},
    ])
    assert data._portfolio_series() == []

    _write_jsonl(tmp_path / "portfolio_history.jsonl", [
        {"timestamp": "2026-01-01T00:00:00+00:00", "pool": "crypto", "total_value": 100.0},
        {"timestamp": "2026-01-01T00:05:00+00:00", "pool": "stocks", "total_value": 900.0},
        {"timestamp": "2026-01-01T00:10:00+00:00", "pool": "crypto", "total_value": 120.0},
    ])
    series = data._portfolio_series()
    assert len(series) == 2
    assert series[0]["total_value"] == 1000.0
    assert series[1]["total_value"] == 1020.0


def test_performance_stats_reports_insufficient_data_honestly():
    tiny_series = [{"timestamp": f"t{i}", "total_value": 1000.0 + i} for i in range(5)]
    result = data._performance_stats(tiny_series)
    assert result["available"] is False
    assert result["points"] == 5


def test_performance_stats_computes_drawdown_and_return_with_enough_points():
    values = [1000.0] * 15 + [900.0] + [1000.0] * 10
    series = [{"timestamp": f"t{i}", "total_value": v} for i, v in enumerate(values)]
    result = data._performance_stats(series)
    assert result["available"] is True
    assert result["max_drawdown_pct"] == -10.0
    assert result["total_return_pct"] == 0.0


def test_open_risk_usd_sums_worst_case_across_pools():
    positions = {
        "stocks": {"AAA": {"entry_price": 100.0, "stop": 90.0, "qty": 10}},
        "crypto": {"BTC-USDT": {"entry_price": 50000.0, "stop": 49000.0, "qty": 0.1}},
    }
    assert data._open_risk_usd(positions) == 200.0


def test_open_risk_usd_ignores_positions_already_above_entry_stop_inverted():
    positions = {"stocks": {"AAA": {"entry_price": 100.0, "stop": 110.0, "qty": 10}}}
    assert data._open_risk_usd(positions) == 0.0


def test_live_positions_computes_pnl_from_marks_and_none_when_no_mark():
    positions = {"stocks": {"AAA": {"entry_price": 100.0, "stop": 90.0, "qty": 10}}}
    with_price = data._live_positions(positions, [], {("stocks", "AAA"): 110.0})[0]
    assert with_price["pnl_usd"] == 100.0
    assert with_price["pnl_pct"] == 10.0

    without_price = data._live_positions(positions, [], {})[0]
    assert without_price["current_price"] is None
    assert without_price["pnl_usd"] is None


def test_position_thesis_pulls_the_matching_executed_decision():
    decisions = [
        {"pool": "stocks", "result": "executed", "signal": {"symbol": "AAA", "opportunity_score": 70},
         "review": {"confidence": 0.7, "reasoning": "test"}, "timestamp": "t1"},
        {"pool": "stocks", "result": "executed", "signal": {"symbol": "BBB"}, "timestamp": "t2"},
    ]
    thesis = data._position_thesis(decisions, "stocks", "AAA")
    assert thesis["signal"]["opportunity_score"] == 70
    assert data._position_thesis(decisions, "crypto", "AAA") is None


def test_closed_trades_reads_stopped_out_events_and_excludes_other_pools():
    decisions = [
        {"pool": "smoketest", "result": "stopped_out", "symbol": "BTC-USDT", "timestamp": "t0", "price": 1.0},
        {"pool": "stocks", "result": "executed", "timestamp": "t1",
         "signal": {"symbol": "AAA"}, "sizing": {"bucket": "momentum", "usd": 500.0},
         "review": {"reasoning": "buena tesis"}},
        {"pool": "stocks", "result": "stopped_out", "symbol": "AAA", "timestamp": "t2",
         "price": 95.0, "stop": 90.0, "won": False},
        {"pool": "stocks", "result": "skipped", "symbol": "BBB", "timestamp": "t3"},
    ]
    closed = data._closed_trades(decisions)
    assert len(closed) == 1
    assert closed[0]["symbol"] == "AAA"
    assert closed[0]["exit_price"] == 95.0
    assert closed[0]["bucket"] == "momentum"
    assert closed[0]["reasoning"] == "buena tesis"


def test_closed_trades_matches_the_thesis_active_at_that_closure_not_a_later_rebuy():
    decisions = [
        {"pool": "stocks", "result": "executed", "timestamp": "t1",
         "signal": {"symbol": "AAA"}, "sizing": {"bucket": "core"}, "review": {"reasoning": "primera compra"}},
        {"pool": "stocks", "result": "stopped_out", "symbol": "AAA", "timestamp": "t2", "price": 95.0},
        {"pool": "stocks", "result": "executed", "timestamp": "t3",
         "signal": {"symbol": "AAA"}, "sizing": {"bucket": "moonshot"}, "review": {"reasoning": "recompra"}},
    ]
    closed = data._closed_trades(decisions)
    assert len(closed) == 1
    assert closed[0]["bucket"] == "core"
    assert closed[0]["reasoning"] == "primera compra"


def test_strategy_performance_labels_low_sample_as_awaiting_results(monkeypatch):
    monkeypatch.setattr(data, "_standings", lambda: [
        {"strategy": "breakout", "scored": 0, "open_proposals": 100, "win_rate": None,
         "avg_return_pct": None, "best_pct": None, "worst_pct": None},
        {"strategy": "momentum", "scored": 30, "open_proposals": 5, "win_rate": 55.0,
         "avg_return_pct": 2.1, "best_pct": 10.0, "worst_pct": -5.0},
    ])
    result = data._strategy_performance()
    assert result[0]["status"] == "awaiting_results"
    assert result[1]["status"] == "scored"


def test_build_data_cache_invalidates_on_file_change(tmp_path, monkeypatch):
    monkeypatch.setattr(data, "LOGS_DIR", tmp_path / "logs")
    monkeypatch.setattr(data, "STATE_DIR", tmp_path / "state")
    (tmp_path / "logs").mkdir()
    (tmp_path / "state").mkdir()
    data._cache["key"] = None
    data._cache["data"] = None

    first = data.build_data()
    second = data.build_data()
    assert first is not second
    assert data._cache["data"] is not None

    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()
    _write_jsonl(tmp_path / "logs" / "decisions.log", [{"pool": "stocks", "result": "market_closed", "timestamp": now}])
    third = data.build_data()
    assert third["pools"]["stocks"]["last_result"] == "market_closed"


def test_build_data_is_json_serializable_on_a_fresh_install(tmp_path, monkeypatch):
    monkeypatch.setattr(data, "LOGS_DIR", tmp_path / "logs")
    monkeypatch.setattr(data, "STATE_DIR", tmp_path / "state")
    data._cache["key"] = None
    data._cache["data"] = None
    result = data.build_data()
    json.dumps(result)
    assert result["portfolio"]["total_capital"] is None
    assert result["performance"]["available"] is False
    assert result["live_positions"] == []


def test_task_statuses_reports_error_string_instead_of_raising(monkeypatch):
    def fake_run(*args, **kwargs):
        raise FileNotFoundError("schtasks no encontrado")
    monkeypatch.setattr(bot_control.subprocess, "run", fake_run)

    statuses = bot_control.task_statuses()
    assert set(statuses.keys()) == set(bot_control.BOT_TASKS)
    assert all(v.startswith("error:") for v in statuses.values())


def test_task_statuses_parses_the_real_schtasks_output_shape(monkeypatch):
    class FakeResult:
        stdout = "TaskName:      \\TradingAgentPaper\nStatus:        Ready\n"
        stderr = ""
    monkeypatch.setattr(bot_control.subprocess, "run", lambda *a, **k: FakeResult())

    statuses = bot_control.task_statuses()
    assert all(v == "Ready" for v in statuses.values())


def test_set_tasks_enabled_is_best_effort_per_task(monkeypatch):
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        if "TradingAgentCrypto" in cmd:
            raise bot_control.subprocess.CalledProcessError(1, cmd)
        class Ok:
            pass
        return Ok()

    monkeypatch.setattr(bot_control.subprocess, "run", fake_run)
    results = bot_control.set_tasks_enabled(True)

    assert results["TradingAgentPaper"] == "ok"
    assert results["TradingAgentCrypto"].startswith("error:")
    assert results["TradingAgentWatchlist"] == "ok"
    assert len(calls) == 3
    assert all("/ENABLE" in c for c in calls)


def test_set_tasks_enabled_disable_uses_the_disable_flag(monkeypatch):
    calls = []
    monkeypatch.setattr(bot_control.subprocess, "run", lambda cmd, **k: calls.append(cmd))
    bot_control.set_tasks_enabled(False)
    assert all("/DISABLE" in c for c in calls)


def test_bot_tasks_list_is_never_derived_from_request_input():
    assert bot_control.BOT_TASKS == ["TradingAgentPaper", "TradingAgentCrypto", "TradingAgentWatchlist"]


# ---- Contrato HTTP real, via TestClient (no existia con http.server) ----

client = TestClient(app)


def test_data_route_matches_the_declared_schema_on_a_fresh_install(tmp_path, monkeypatch):
    """El contrato tiene que servir 200 y validar contra DashboardData
    incluso en una instalacion sin ningun log -- si esto rompe, rompe la
    build de TypeScript entera, no solo un widget."""
    monkeypatch.setattr(data, "LOGS_DIR", tmp_path / "logs")
    monkeypatch.setattr(data, "STATE_DIR", tmp_path / "state")
    data._cache["key"] = None
    data._cache["data"] = None

    response = client.get("/data")
    assert response.status_code == 200
    body = response.json()
    assert body["live_positions"] == []
    assert body["portfolio"]["total_capital"] is None


def test_bot_status_route_returns_one_entry_per_task(monkeypatch):
    monkeypatch.setattr(bot_control, "task_statuses", lambda: {"TradingAgentPaper": "Disabled"})
    response = client.get("/bot-status")
    assert response.status_code == 200
    assert response.json() == {"tasks": {"TradingAgentPaper": "Disabled"}}


def test_bot_start_and_stop_never_act_on_a_get_request(monkeypatch):
    """Lo que realmente importa no es el codigo de status exacto -- que
    puede variar segun si dashboard-web/dist existe (con dist/, el
    catch-all que sirve el SPA intercepta un GET perdido a estas rutas y
    devuelve el index.html en vez de un 404/405; sin dist/, esas rutas ni
    se registran) -- sino que un GET nunca puede terminar prendiendo o
    apagando las tareas programadas. Esto es lo que hace seguro exponer
    el control por HTTP."""
    calls = []
    monkeypatch.setattr(bot_control, "set_tasks_enabled", lambda enabled: calls.append(enabled))

    client.get("/bot-start")
    client.get("/bot-stop")

    assert calls == []


def test_market_radar_route_is_json_serializable_and_typed(monkeypatch):
    from signals.market_radar import DexMover, Mover

    monkeypatch.setattr(market_radar, "cex_movers", lambda: [
        Mover(symbol="BINANCE:DOGEUSDT", exchange="Binance", price=0.15,
              change_24h_pct=42.3, volume_24h_usd=5_000_000.0, suspicious=False),
    ])
    monkeypatch.setattr(market_radar, "dex_movers", lambda: [
        DexMover(symbol="RAYDIUM:FOO", exchange="Raydium", blockchain="Solana",
                 price=0.002, change_24h_pct=88.0, volume_24h_usd=60_000.0, suspicious=False),
    ])

    response = client.get("/market-radar")
    assert response.status_code == 200
    body = response.json()
    assert body["cex_movers"][0]["symbol"] == "BINANCE:DOGEUSDT"
    assert body["dex_movers"][0]["blockchain"] == "Solana"


def test_market_radar_caches_for_60_seconds(monkeypatch):
    calls = []
    monkeypatch.setattr(market_radar, "cex_movers", lambda: calls.append(1) or [])
    monkeypatch.setattr(market_radar, "dex_movers", lambda: [])
    import api.app as app_module
    app_module._radar_cache["ts"] = 0.0
    app_module._radar_cache["data"] = None

    client.get("/market-radar")
    client.get("/market-radar")
    assert len(calls) == 1  # la segunda llamada uso el cache, no pego de nuevo a TradingView
