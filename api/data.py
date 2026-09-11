"""Computa el payload de /data.

Logica movida tal cual desde el dashboard.py anterior -- ya estaba
correcta y testeada (tests/test_dashboard.py), lo unico que cambia con
esta migracion es el seam por el que se expone (FastAPI+Pydantic en vez
de http.server + json.dumps a mano), no la implementacion.
"""
import json
from datetime import datetime, time, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from config.limits import MAX_DEPLOYED_PCT, MAX_OPEN_POSITIONS

ROOT = Path(__file__).resolve().parent.parent
LOGS_DIR = ROOT / "logs"
STATE_DIR = ROOT / "state"

# La ventana en la que TIENE sentido esperar que el pool de acciones haya
# tickeado, para no marcarlo "stale" de noche o el fin de semana.
#
# Se expresa en hora de Nueva York, igual que _market_is_open() del
# orchestrator. Antes era hora LOCAL de la maquina: en esta (UTC-5) la
# ventana caia una hora corrida respecto del mercado, y si la maquina
# cambia de zona horaria -- cosa que paso durante esta misma auditoria --
# la ventana se mueve sola. Dashboard y bot tienen que estar de acuerdo
# sobre cuando el mercado esta abierto (seccion 45).
_NY = ZoneInfo("America/New_York")
_STOCKS_WINDOW_START = time(9, 0)
_STOCKS_WINDOW_END = time(16, 30)

# Bajo esta cantidad de puntos, Sharpe/volatilidad/drawdown son ruido
# estadistico, no una metrica -- se muestran como "N/A: falta historial"
# en vez de un numero que aparenta precision que no existe.
_MIN_POINTS_FOR_STATS = 20


def _tail_jsonl(path: Path, n: int) -> list[dict]:
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines()[-n:]:
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    return out


def _read_json(path: Path) -> dict | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except ValueError:
        return None


def _age_minutes(iso_timestamp: str) -> float:
    ts = datetime.fromisoformat(iso_timestamp)
    return (datetime.now(timezone.utc) - ts).total_seconds() / 60


def _within_stocks_window(now: datetime | None = None) -> bool:
    """Si el mercado de acciones esta en horario habil, en la zona horaria
    del mercado -- no en la de la maquina. Funcion aparte y con reloj
    inyectable para que sea testeable sin depender de cuando corren los
    tests ni de donde este la maquina."""
    now_ny = (now or datetime.now(timezone.utc)).astimezone(_NY)
    if now_ny.weekday() >= 5:
        return False
    return _STOCKS_WINDOW_START <= now_ny.time() < _STOCKS_WINDOW_END


def _pool_health(decisions: list[dict], pool: str) -> dict:
    last = next((e for e in reversed(decisions) if e.get("pool") in (pool, "both") and "timestamp" in e), None)
    if not last:
        return {"last_result": None, "last_timestamp": None, "age_minutes": None, "stale": True}

    age = _age_minutes(last["timestamp"])
    if pool == "crypto":
        stale = age > 90  # ticks every 60min, 24/7
    else:
        stale = age > 90 and _within_stocks_window()
    return {"last_result": last.get("result"), "last_timestamp": last["timestamp"], "age_minutes": round(age, 1), "stale": stale}


def _standings() -> list[dict]:
    """Tabla del torneo, o lista vacia si aun no se creo -- el dashboard
    tiene que abrir bien en una maquina que nunca corrio el orchestrator."""
    try:
        from dataclasses import asdict
        from execution.tournament import standings
        return [asdict(s) for s in standings(STATE_DIR / "tournament.db")]
    except Exception:
        return []


def _panel_sentiment(decisions: list[dict], pool: str = "crypto", window: int = 50) -> dict | None:
    """Share of recent panel votes that said buy, from real decision
    history -- never fabricated. None once there's nothing to compute
    yet (a fresh install, or a pool that hasn't run)."""
    reviews = [d["review"]["action"] for d in decisions if d.get("pool") == pool and "review" in d]
    reviews = reviews[-window:]
    if not reviews:
        return None
    buys = sum(1 for a in reviews if a == "buy")
    return {"buy_pct": round(buys / len(reviews) * 100, 1), "sample": len(reviews)}


def _watchlist_status() -> dict:
    """Real data only: the actual state/watchlist.json plus the most recent
    watchlist_updated/watchlist_update_error log line, so the page shows
    why today's list looks the way it does -- or that the daily refresh
    failed and this is stale, rather than silently showing an old list
    with no indication anything's wrong."""
    symbols = _read_json(STATE_DIR / "watchlist.json") or []
    entries = _tail_jsonl(LOGS_DIR / "decisions.log", 3000)
    last_update = next((e for e in reversed(entries) if e.get("result") == "watchlist_updated"), None)
    last_error = next((e for e in reversed(entries) if e.get("result") == "watchlist_update_error"), None)

    age = _age_minutes(last_update["timestamp"]) if last_update else None
    stale = age is None or age > 30 * 60
    show_error = last_error is not None and (last_update is None or last_error["timestamp"] > last_update["timestamp"])

    return {
        "symbols": symbols,
        "detail": (last_update or {}).get("detail") or [],
        "last_updated": (last_update or {}).get("timestamp"),
        "age_minutes": round(age, 1) if age is not None else None,
        "stale": stale,
        "last_error": last_error.get("reason") if show_error else None,
    }


def _last_scan() -> dict | None:
    for entry in reversed(_tail_jsonl(LOGS_DIR / "decisions.log", 500)):
        if entry.get("result") == "tournament":
            return entry
    return None


def _last_matching(decisions: list[dict], pool: str, result: str | None = None) -> dict | None:
    for e in reversed(decisions):
        if e.get("pool") not in (pool, "both") or "timestamp" not in e:
            continue
        if result is not None and e.get("result") != result:
            continue
        return e
    return None


def _discovery(decisions: list[dict]) -> dict:
    return {pool: _last_matching(decisions, pool, "scan") for pool in ("stocks", "crypto")}


def _deployment_alert(decisions: list[dict], pool: str) -> dict | None:
    last = _last_matching(decisions, pool)
    if not last or last.get("result") != "capital_deployment_alert":
        return None
    return {
        "timestamp": last["timestamp"],
        "age_minutes": round(_age_minutes(last["timestamp"]), 1),
        "diagnosis": last.get("diagnosis"),
        "detail": last.get("detail"),
        "assets_scanned": last.get("assets_scanned"),
        "shortlisted": last.get("shortlisted"),
    }


def _bucket_capital(trades: list[dict]) -> dict:
    buckets: dict[str, float] = {}
    for t in trades:
        sizing = t.get("sizing")
        if sizing and sizing.get("bucket"):
            buckets[sizing["bucket"]] = round(buckets.get(sizing["bucket"], 0) + sizing.get("usd", 0), 2)
    return buckets


def _live_pool_value(pool: str, breaker: dict | None) -> float | None:
    rows = [r for r in _tail_jsonl(LOGS_DIR / "portfolio_history.jsonl", 2000) if r.get("pool") == pool]
    if rows:
        return rows[-1]["total_value"]
    return (breaker or {}).get("day_start_value")


def _portfolio_series(max_points: int = 2000) -> list[dict]:
    rows = sorted(_tail_jsonl(LOGS_DIR / "portfolio_history.jsonl", max_points), key=lambda r: r["timestamp"])
    last: dict[str, float] = {}
    out = []
    for r in rows:
        last[r["pool"]] = r["total_value"]
        if "stocks" in last and "crypto" in last:
            out.append({"timestamp": r["timestamp"], "total_value": round(last["stocks"] + last["crypto"], 2)})
    return out


def _performance_stats(series: list[dict]) -> dict:
    if len(series) < _MIN_POINTS_FOR_STATS:
        return {"available": False, "reason": f"se necesitan {_MIN_POINTS_FOR_STATS} puntos, hay {len(series)}",
                "points": len(series)}

    values = [p["total_value"] for p in series]
    returns = [(values[i] - values[i - 1]) / values[i - 1] for i in range(1, len(values)) if values[i - 1] > 0]
    peak = values[0]
    max_dd = 0.0
    for v in values:
        peak = max(peak, v)
        if peak > 0:
            max_dd = min(max_dd, (v - peak) / peak)

    total_return_pct = (values[-1] - values[0]) / values[0] * 100 if values[0] > 0 else None
    mean_r = sum(returns) / len(returns) if returns else 0.0
    variance = sum((r - mean_r) ** 2 for r in returns) / len(returns) if returns else 0.0
    return {
        "available": True,
        "points": len(series),
        "total_return_pct": round(total_return_pct, 2) if total_return_pct is not None else None,
        "max_drawdown_pct": round(max_dd * 100, 2),
        "volatility_pct": round((variance ** 0.5) * 100, 3),
    }


def _open_risk_usd(positions: dict) -> float:
    return sum(
        max(0.0, (pos["entry_price"] - pos["stop"]) * pos["qty"])
        for pool in positions.values() for pos in pool.values()
        if pos.get("entry_price") and pos.get("stop") and pos.get("qty")
    )


def _position_thesis(decisions: list[dict], pool: str, symbol: str, before_ts: str | None = None) -> dict | None:
    for e in reversed(decisions):
        if e.get("pool") != pool or e.get("result") != "executed":
            continue
        if (e.get("signal") or {}).get("symbol") != symbol:
            continue
        if before_ts is not None and e.get("timestamp", "") >= before_ts:
            continue
        return e
    return None


def _latest_marks() -> dict:
    marks: dict = {}
    for m in _tail_jsonl(LOGS_DIR / "position_marks.jsonl", 5000):
        if "symbol" in m and "price" in m:
            marks[(m.get("pool"), m["symbol"])] = m["price"]
    return marks


def _live_positions(positions: dict, decisions: list[dict], live_prices: dict) -> list[dict]:
    out = []
    for pool, syms in positions.items():
        for symbol, pos in syms.items():
            entry, qty, stop = pos["entry_price"], pos["qty"], pos["stop"]
            price = live_prices.get((pool, symbol))
            thesis = _position_thesis(decisions, pool, symbol)
            market_value = (price or entry) * qty
            pnl = (price - entry) * qty if price is not None else None
            out.append({
                "pool": pool, "symbol": symbol, "entry_price": entry, "current_price": price,
                "qty": qty, "stop": stop, "market_value": round(market_value, 2),
                "pnl_usd": round(pnl, 2) if pnl is not None else None,
                "pnl_pct": round((price - entry) / entry * 100, 2) if price and entry else None,
                "risk_usd": round(max(0.0, (entry - stop) * qty), 2),
                "opened_at": thesis.get("timestamp") if thesis else None,
                "strategy": ", ".join((thesis or {}).get("signal", {}).get("strategies", []) or []) or None,
                "bucket": ((thesis or {}).get("sizing") or {}).get("bucket"),
                "confidence": ((thesis or {}).get("review") or {}).get("confidence"),
                "reasoning": ((thesis or {}).get("review") or {}).get("reasoning"),
                "asymmetry": (thesis or {}).get("signal", {}).get("asymmetry"),
                "opportunity_score": (thesis or {}).get("signal", {}).get("opportunity_score"),
            })
    out.sort(key=lambda p: p["market_value"], reverse=True)
    return out


def _risk_center(positions: dict, live_positions: list[dict], live_values: dict, spend: dict) -> dict:
    total = sum(v for v in live_values.values() if v)
    exposure_by_pool = {
        pool: round(sum(p["market_value"] for p in live_positions if p["pool"] == pool), 2)
        for pool in ("stocks", "crypto")
    }
    bucket_exposure: dict[str, float] = {}
    for p in live_positions:
        b = p.get("bucket") or "sin_clasificar"
        bucket_exposure[b] = round(bucket_exposure.get(b, 0) + p["market_value"], 2)

    largest = max(live_positions, key=lambda p: p["market_value"], default=None)
    exposure_pct = round(sum(exposure_by_pool.values()) / total * 100, 1) if total else None

    alerts = []
    if largest and total and largest["market_value"] / total > 0.10:
        alerts.append({"level": "warning", "code": "CONCENTRATION_RISK",
                       "detail": f"{largest['symbol']} es {largest['market_value']/total:.1%} del portafolio"})
    if exposure_pct is not None and exposure_pct < 15:
        alerts.append({"level": "info", "code": "EXCESS_CASH",
                       "detail": f"solo {exposure_pct:.1f}% desplegado -- ver Bot Activity para saber si es por falta de oportunidades o por filtros"})

    return {
        "total_capital": round(total, 2) if total else None,
        "exposure_pct": exposure_pct,
        "exposure_by_pool": exposure_by_pool,
        "bucket_exposure": bucket_exposure,
        "open_risk_usd": round(_open_risk_usd(positions), 2),
        "largest_position": {"symbol": largest["symbol"], "pool": largest["pool"], "market_value": largest["market_value"]} if largest else None,
        "n_positions": len(live_positions),
        "max_open_positions": MAX_OPEN_POSITIONS,
        "max_deployed_pct": round(MAX_DEPLOYED_PCT * 100, 1),
        "spend_today": spend,
        "alerts": alerts,
    }


def _closed_trades(decisions: list[dict]) -> list[dict]:
    out = []
    for e in decisions:
        if e.get("result") != "stopped_out" or e.get("pool") not in ("stocks", "crypto"):
            continue
        thesis = _position_thesis(decisions, e["pool"], e["symbol"], before_ts=e.get("timestamp"))
        sizing = (thesis or {}).get("sizing") or {}
        out.append({
            "pool": e["pool"], "symbol": e["symbol"], "exit_price": e.get("price"),
            "stop": e.get("stop"), "won": e.get("won"), "closed_at": e.get("timestamp"),
            "opened_at": thesis.get("timestamp") if thesis else None,
            "bucket": sizing.get("bucket"), "position_usd": sizing.get("usd"),
            "reasoning": ((thesis or {}).get("review") or {}).get("reasoning"),
        })
    return out


def _strategy_performance() -> list[dict]:
    out = []
    for s in _standings():
        out.append({**s, "status": "scored" if s["scored"] >= 5 else "awaiting_results"})
    return out


def _rejection_totals(discovery: dict) -> dict:
    totals: dict[str, int] = {}
    for entry in discovery.values():
        if not entry:
            continue
        for reason, count in (entry.get("rejection_reasons") or {}).items():
            totals[reason] = totals.get(reason, 0) + count
    return totals


def _portfolio(positions: dict, live_values: dict, trades_wide: list[dict]) -> dict:
    deployed = sum(pos.get("entry_price", 0) * pos.get("qty", 0) for pool in positions.values() for pos in pool.values())
    total = sum(v for v in live_values.values() if v)
    n_positions = sum(len(pool) for pool in positions.values())
    return {
        "total_capital": round(total, 2) if total else None,
        "deployed_capital": round(deployed, 2),
        "cash_pct": round((total - deployed) / total * 100, 1) if total else None,
        "n_positions": n_positions,
        "buckets": _bucket_capital(trades_wide),
    }


# Cache keyed on the mtime of every file build_data() reads. El nuevo
# frontend sigue sondeando cada 5s (51KB reales medidos en un dia normal,
# irrelevante) mientras el orchestrator escribe cada 15-30min -- sin esto
# se re-parsearia decisions.log entero en cada poll.
_cache: dict = {"key": None, "data": None}


def _watched_files() -> list[Path]:
    return [
        LOGS_DIR / "decisions.log", LOGS_DIR / "trades.log", LOGS_DIR / "portfolio_history.jsonl",
        LOGS_DIR / "position_marks.jsonl",
        STATE_DIR / "positions_stocks.json", STATE_DIR / "positions_crypto.json",
        STATE_DIR / "breaker_stocks.json", STATE_DIR / "breaker_crypto.json",
        STATE_DIR / "spend_stocks.json", STATE_DIR / "spend_crypto.json",
        STATE_DIR / "watchlist.json", STATE_DIR / "tournament.db",
    ]


def _cache_key() -> tuple:
    return tuple(p.stat().st_mtime_ns if p.exists() else 0 for p in _watched_files())


def build_data() -> dict:
    key = _cache_key()
    if _cache["key"] == key and _cache["data"] is not None:
        data = dict(_cache["data"])
        data["generated_at"] = datetime.now(timezone.utc).isoformat()
        return data

    decisions = _tail_jsonl(LOGS_DIR / "decisions.log", 800)
    trades = _tail_jsonl(LOGS_DIR / "trades.log", 20)
    trades_wide = _tail_jsonl(LOGS_DIR / "trades.log", 500)
    positions = {p: v for p in ("stocks", "crypto") if (v := _read_json(STATE_DIR / f"positions_{p}.json"))}
    breakers = {p: v for p in ("stocks", "crypto") if (v := _read_json(STATE_DIR / f"breaker_{p}.json"))}
    spend = {p: v for p in ("stocks", "crypto") if (v := _read_json(STATE_DIR / f"spend_{p}.json"))}
    live_values = {p: _live_pool_value(p, breakers.get(p)) for p in ("stocks", "crypto")}

    errors = [e for e in decisions if e.get("result") == "error" and "timestamp" in e and _age_minutes(e["timestamp"]) < 24 * 60]
    recent = [d for d in decisions if "timestamp" in d and _age_minutes(d["timestamp"]) < 48 * 60]
    feed = [d for d in recent if d.get("result") not in ("tournament", "scan")]

    live_pos = _live_positions(positions, decisions, _latest_marks())
    discovery = _discovery(decisions)
    series = _portfolio_series()

    data = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "pools": {pool: _pool_health(decisions, pool) for pool in ("stocks", "crypto")},
        "positions": positions,
        "live_positions": live_pos,
        "breakers": breakers,
        "spend": spend,
        "standings": _standings(),
        "strategy_performance": _strategy_performance(),
        "watchlist": _watchlist_status(),
        "last_scan": _last_scan(),
        "panel_sentiment": _panel_sentiment(decisions),
        "discovery": discovery,
        "rejection_totals": _rejection_totals(discovery),
        "deployment_alerts": {p: a for p in ("stocks", "crypto") if (a := _deployment_alert(decisions, p))},
        "portfolio": _portfolio(positions, live_values, trades_wide),
        "portfolio_series": series,
        "performance": _performance_stats(series),
        "risk": _risk_center(positions, live_pos, live_values, spend),
        "closed_trades": _closed_trades(decisions),
        "error_count": len(errors),
        "last_error": errors[-1] if errors else None,
        "recent_decisions": list(reversed(feed))[:30],
        "recent_trades": list(reversed(trades)),
    }
    _cache["key"], _cache["data"] = key, data
    return data


def backtest_payload(symbol: str, strategy: str, period: str = "2y") -> dict:
    """Backtest de una estrategia sobre un simbolo, listo para graficar.

    Import local de yfinance a proposito: este modulo lo carga el dashboard
    en cada arranque y no puede pagar esa dependencia solo porque exista
    esta ruta.
    """
    import yfinance as yf

    from backtest.engine import replay_strategy, summarize
    from signals.strategies import ALL_STRATEGIES

    from signals import custom

    custom_specs = custom.load_all(STATE_DIR)
    if strategy in ALL_STRATEGIES:
        fn = ALL_STRATEGIES[strategy]
    elif strategy in custom_specs:
        fn = custom_specs[strategy].as_fn()
    else:
        valid = sorted(set(ALL_STRATEGIES) | set(custom_specs))
        raise ValueError(f"estrategia desconocida: {strategy!r}. Validas: {valid}")

    hist = yf.Ticker(symbol).history(period=period)
    if hist.empty or "Close" not in hist:
        raise ValueError(f"sin datos de mercado para {symbol!r}")

    closes = [float(x) for x in hist["Close"]]
    highs = [float(x) for x in hist["High"]]
    lows = [float(x) for x in hist["Low"]]
    opens = [float(x) for x in hist["Open"]]
    times = [int(ts.timestamp()) for ts in hist.index]

    trades = replay_strategy(closes, fn, symbol=symbol, highs=highs, lows=lows)
    result = summarize("stocks", str(times[0]), str(times[-1]), trades)

    def _t(idx: str | None) -> int | None:
        i = int(idx) if idx is not None else None
        return times[i] if i is not None and 0 <= i < len(times) else None

    return {
        "symbol": symbol,
        "strategy": strategy,
        "period": period,
        "metrics": {
            "closed_trades": len([t for t in trades if t.pnl_pct is not None]),
            "total_return_pct": result.total_return_pct,
            "max_drawdown_pct": result.max_drawdown_pct,
            "win_rate": result.win_rate,
            "sharpe": result.sharpe,
            "sortino": result.sortino,
            "calmar": result.calmar,
        },
        "equity_curve": result.equity_curve,
        "bars": [
            {"time": times[i], "open": opens[i], "high": highs[i], "low": lows[i], "close": closes[i]}
            for i in range(len(times))
        ],
        "trades": [
            {
                "entry_time": _t(t.entry_date), "entry_price": t.entry_price,
                "exit_time": _t(t.exit_date), "exit_price": t.exit_price,
                "exit_reason": t.exit_reason, "pnl_pct": t.pnl_pct,
            }
            for t in trades
        ],
        "available_strategies": sorted(set(ALL_STRATEGIES) | set(custom_specs)),
    }
