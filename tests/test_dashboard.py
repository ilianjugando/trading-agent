import json

import dashboard


def _write_jsonl(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n" if rows else "")


def test_portfolio_series_requires_both_pools_before_emitting_a_point(tmp_path, monkeypatch):
    monkeypatch.setattr(dashboard, "LOGS_DIR", tmp_path)
    _write_jsonl(tmp_path / "portfolio_history.jsonl", [
        {"timestamp": "2026-01-01T00:00:00+00:00", "pool": "crypto", "total_value": 100.0},
        {"timestamp": "2026-01-01T00:05:00+00:00", "pool": "crypto", "total_value": 110.0},
    ])
    # Solo crypto reporto -- sumar esto solo mostraria una fraccion del
    # portafolio real como si fuera el total.
    assert dashboard._portfolio_series() == []

    _write_jsonl(tmp_path / "portfolio_history.jsonl", [
        {"timestamp": "2026-01-01T00:00:00+00:00", "pool": "crypto", "total_value": 100.0},
        {"timestamp": "2026-01-01T00:05:00+00:00", "pool": "stocks", "total_value": 900.0},
        {"timestamp": "2026-01-01T00:10:00+00:00", "pool": "crypto", "total_value": 120.0},
    ])
    series = dashboard._portfolio_series()
    assert len(series) == 2  # el primer evento (solo crypto) sigue sin contar
    assert series[0]["total_value"] == 1000.0  # 100 (crypto) + 900 (stocks, recien conocido)
    assert series[1]["total_value"] == 1020.0  # 120 (crypto nuevo) + 900 (stocks, forward-filled)


def test_performance_stats_reports_insufficient_data_honestly():
    tiny_series = [{"timestamp": f"t{i}", "total_value": 1000.0 + i} for i in range(5)]
    result = dashboard._performance_stats(tiny_series)
    assert result["available"] is False
    assert result["points"] == 5


def test_performance_stats_computes_drawdown_and_return_with_enough_points():
    values = [1000.0] * 15 + [900.0] + [1000.0] * 10  # dip to 900 then recover
    series = [{"timestamp": f"t{i}", "total_value": v} for i, v in enumerate(values)]
    result = dashboard._performance_stats(series)
    assert result["available"] is True
    assert result["max_drawdown_pct"] == -10.0
    assert result["total_return_pct"] == 0.0


def test_open_risk_usd_sums_worst_case_across_pools():
    positions = {
        "stocks": {"AAA": {"entry_price": 100.0, "stop": 90.0, "qty": 10}},
        "crypto": {"BTC-USDT": {"entry_price": 50000.0, "stop": 49000.0, "qty": 0.1}},
    }
    # (100-90)*10 = 100, (50000-49000)*0.1 = 100
    assert dashboard._open_risk_usd(positions) == 200.0


def test_open_risk_usd_ignores_positions_already_above_entry_stop_inverted():
    # Un stop por encima de la entrada seria un dato corrupto -- no debe
    # restar (dar "riesgo negativo"), se clampea a 0.
    positions = {"stocks": {"AAA": {"entry_price": 100.0, "stop": 110.0, "qty": 10}}}
    assert dashboard._open_risk_usd(positions) == 0.0


def test_live_positions_computes_pnl_from_marks_and_none_when_no_mark():
    positions = {"stocks": {"AAA": {"entry_price": 100.0, "stop": 90.0, "qty": 10}}}
    with_price = dashboard._live_positions(positions, [], {("stocks", "AAA"): 110.0})[0]
    assert with_price["pnl_usd"] == 100.0
    assert with_price["pnl_pct"] == 10.0

    without_price = dashboard._live_positions(positions, [], {})[0]
    assert without_price["current_price"] is None
    assert without_price["pnl_usd"] is None


def test_position_thesis_pulls_the_matching_executed_decision():
    decisions = [
        {"pool": "stocks", "result": "executed", "signal": {"symbol": "AAA", "opportunity_score": 70},
         "review": {"confidence": 0.7, "reasoning": "test"}, "timestamp": "t1"},
        {"pool": "stocks", "result": "executed", "signal": {"symbol": "BBB"}, "timestamp": "t2"},
    ]
    thesis = dashboard._position_thesis(decisions, "stocks", "AAA")
    assert thesis["signal"]["opportunity_score"] == 70
    assert dashboard._position_thesis(decisions, "crypto", "AAA") is None


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
    closed = dashboard._closed_trades(decisions)
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
    closed = dashboard._closed_trades(decisions)
    assert len(closed) == 1
    assert closed[0]["bucket"] == "core"
    assert closed[0]["reasoning"] == "primera compra"


def test_strategy_performance_labels_low_sample_as_awaiting_results(monkeypatch):
    monkeypatch.setattr(dashboard, "_standings", lambda: [
        {"strategy": "breakout", "scored": 0, "open_proposals": 100, "win_rate": None,
         "avg_return_pct": None, "best_pct": None, "worst_pct": None},
        {"strategy": "momentum", "scored": 30, "open_proposals": 5, "win_rate": 55.0,
         "avg_return_pct": 2.1, "best_pct": 10.0, "worst_pct": -5.0},
    ])
    result = dashboard._strategy_performance()
    assert result[0]["status"] == "awaiting_results"
    assert result[1]["status"] == "scored"


def test_build_data_cache_invalidates_on_file_change(tmp_path, monkeypatch):
    monkeypatch.setattr(dashboard, "LOGS_DIR", tmp_path / "logs")
    monkeypatch.setattr(dashboard, "STATE_DIR", tmp_path / "state")
    (tmp_path / "logs").mkdir()
    (tmp_path / "state").mkdir()
    dashboard._cache["key"] = None
    dashboard._cache["data"] = None

    first = dashboard.build_data()
    second = dashboard.build_data()
    assert first is not second  # generated_at siempre se refresca...
    assert dashboard._cache["data"] is not None  # ...pero se sirvio del cache, no de un re-parseo

    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()
    _write_jsonl(tmp_path / "logs" / "decisions.log", [{"pool": "stocks", "result": "market_closed", "timestamp": now}])
    third = dashboard.build_data()
    assert third["pools"]["stocks"]["last_result"] == "market_closed"


def test_build_data_is_json_serializable_on_a_fresh_install(tmp_path, monkeypatch):
    """El dashboard tiene que abrir sin romper en una maquina que nunca
    corrio el orchestrator -- todo vacio, nada de datos."""
    monkeypatch.setattr(dashboard, "LOGS_DIR", tmp_path / "logs")
    monkeypatch.setattr(dashboard, "STATE_DIR", tmp_path / "state")
    dashboard._cache["key"] = None
    dashboard._cache["data"] = None
    data = dashboard.build_data()
    json.dumps(data)
    assert data["portfolio"]["total_capital"] is None
    assert data["performance"]["available"] is False
    assert data["live_positions"] == []
