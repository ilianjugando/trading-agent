"""Local, read-only dashboard for watching the orchestrator between runs.

Run manually when you want to look -- this does NOT run on a schedule
and is never started automatically:

    python dashboard.py

Then open http://127.0.0.1:8787 in a browser. It polls its own /data
endpoint every 5s (the orchestrator only ticks every 30-60min, so that's
more than enough granularity -- no websockets needed).

Binds to 127.0.0.1 only, and serves exactly two routes (no directory
listing) -- it reads .env's own directory but never serves .env itself.
"""
import json
import webbrowser
from dataclasses import asdict
from datetime import datetime, time, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent
LOGS_DIR = ROOT / "logs"
STATE_DIR = ROOT / "state"
PORT = 8787

# The scheduled task's outer window (see Task Scheduler "TradingAgentPaper").
# Used only to avoid flagging stocks as "stale" outside its firing window.
_STOCKS_WINDOW_START = time(7, 30)
_STOCKS_WINDOW_END = time(17, 0)


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


def _pool_health(decisions: list[dict], pool: str) -> dict:
    last = next((e for e in reversed(decisions) if e.get("pool") in (pool, "both") and "timestamp" in e), None)
    if not last:
        return {"last_result": None, "last_timestamp": None, "age_minutes": None, "stale": True}

    age = _age_minutes(last["timestamp"])
    if pool == "crypto":
        stale = age > 90  # ticks every 60min, 24/7
    else:
        in_window = _STOCKS_WINDOW_START <= datetime.now().time() < _STOCKS_WINDOW_END
        stale = age > 90 and in_window
    return {"last_result": last.get("result"), "last_timestamp": last["timestamp"], "age_minutes": round(age, 1), "stale": stale}


def _standings() -> list[dict]:
    """Tournament table, or an empty list if it hasn't been created yet --
    the dashboard must open fine on a machine that has never run the
    orchestrator."""
    try:
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
    # Watchlist updates fire once a day, so 500 lines of decisions.log
    # (busy with 15/30min crypto+stocks ticks) can roll past yesterday's
    # entry -- read further back than build_data()'s other tails.
    entries = _tail_jsonl(LOGS_DIR / "decisions.log", 3000)
    last_update = next((e for e in reversed(entries) if e.get("result") == "watchlist_updated"), None)
    last_error = next((e for e in reversed(entries) if e.get("result") == "watchlist_update_error"), None)

    age = _age_minutes(last_update["timestamp"]) if last_update else None
    # Fires once daily; flag stale past ~30h so a single slightly-late run
    # doesn't false-alarm but a genuinely missed day does.
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
    """The most recent tournament bookkeeping line, so the page can show
    how wide the last scan actually looked."""
    for entry in reversed(_tail_jsonl(LOGS_DIR / "decisions.log", 500)):
        if entry.get("result") == "tournament":
            return entry
    return None


def _last_matching(decisions: list[dict], pool: str, result: str | None = None) -> dict | None:
    """Most recent entry for a pool (or a "both"-pool entry), optionally
    filtered to one result type. Same pool-matching rule as _pool_health."""
    for e in reversed(decisions):
        if e.get("pool") not in (pool, "both") or "timestamp" not in e:
            continue
        if result is not None and e.get("result") != result:
            continue
        return e
    return None


def _discovery(decisions: list[dict]) -> dict:
    """Most recent scan entry per pool -- backs both the discovery funnel
    and the top-candidates table, so a pool that hasn't scanned yet is
    None (rendered as an explicit empty state) rather than fabricated."""
    return {pool: _last_matching(decisions, pool, "scan") for pool in ("stocks", "crypto")}


def _deployment_alert(decisions: list[dict], pool: str) -> dict | None:
    """Only surfaced when the pool's most recent entry (of any result) IS
    the alert -- once a scan or an execution happens after it, the
    paralysis is resolved and the banner should disappear. Normalizes the
    alert's field names (which differ from the scan entry's) into one shape
    for the frontend."""
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
    """Sums sizing.usd by bucket across trades.log entries that carry a
    sizing block (i.e. actual buy orders, not closes)."""
    buckets: dict[str, float] = {}
    for t in trades:
        sizing = t.get("sizing")
        if sizing and sizing.get("bucket"):
            buckets[sizing["bucket"]] = round(buckets.get(sizing["bucket"], 0) + sizing.get("usd", 0), 2)
    return buckets


# Mismos topes que execution/orchestrator.py -- importados, no copiados a
# mano, para que el dashboard nunca pueda mostrar un limite desactualizado
# si algun dia cambian alli.
try:
    from execution.orchestrator import MAX_DEPLOYED_PCT, MAX_OPEN_POSITIONS
except Exception:
    MAX_OPEN_POSITIONS, MAX_DEPLOYED_PCT = 12, 0.60

# Bajo esta cantidad de puntos, Sharpe/volatilidad/drawdown son ruido
# estadistico, no una metrica -- se muestran como "N/A: falta historial"
# en vez de un numero que aparenta precision que no existe.
_MIN_POINTS_FOR_STATS = 20


def _live_pool_value(pool: str, breaker: dict | None) -> float | None:
    """El valor MAS RECIENTE del pool, de portfolio_history.jsonl (que se
    escribe en cada ciclo real, con precio de mercado en vivo) -- no
    `breaker.day_start_value`, que es el valor al ABRIR el dia y se queda
    fijo aunque el precio de las posiciones se mueva."""
    rows = [r for r in _tail_jsonl(LOGS_DIR / "portfolio_history.jsonl", 2000) if r.get("pool") == pool]
    if rows:
        return rows[-1]["total_value"]
    return (breaker or {}).get("day_start_value")


def _portfolio_series(max_points: int = 2000) -> list[dict]:
    """Serie combinada stocks+crypto por forward-fill: en cada evento se usa
    el ultimo valor CONOCIDO del otro pool, asi ambas piernas aportan a una
    sola curva de equity aunque cada una escanee en su propio horario.

    Un punto solo se emite una vez que YA se conoce el valor de los DOS
    pools -- antes de eso, sumar solo lo que llego primero mostraria (por
    ejemplo) $97K de crypto como si fuera el portafolio completo, faltando
    el ~$1M de stocks que simplemente todavia no habia snapshoteado.
    """
    rows = sorted(_tail_jsonl(LOGS_DIR / "portfolio_history.jsonl", max_points), key=lambda r: r["timestamp"])
    last: dict[str, float] = {}
    out = []
    for r in rows:
        last[r["pool"]] = r["total_value"]
        if "stocks" in last and "crypto" in last:
            out.append({"timestamp": r["timestamp"], "total_value": round(last["stocks"] + last["crypto"], 2)})
    return out


def _performance_stats(series: list[dict]) -> dict:
    """Retorno, drawdown y volatilidad -- solo si hay muestra suficiente
    para que el numero signifique algo. `insufficient_data` no es un
    error: es el estado real y honesto de un sistema que recien empieza a
    registrar su propia historia."""
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
    """Cuanto se pierde EN TOTAL si cada posicion abierta toca su stop a la
    vez -- el peor caso simultaneo, no una suma de probabilidades."""
    return sum(
        max(0.0, (pos["entry_price"] - pos["stop"]) * pos["qty"])
        for pool in positions.values() for pos in pool.values()
        if pos.get("entry_price") and pos.get("stop") and pos.get("qty")
    )


def _position_thesis(decisions: list[dict], pool: str, symbol: str, before_ts: str | None = None) -> dict | None:
    """La decision `executed` mas reciente para este simbolo -- ahi vive la
    tesis real (review.reasoning), la asimetria medida al entrar, y el
    bucket/tamano. Sin esto una posicion es solo 3 numeros sin motivo.

    `before_ts` importa para una posicion ya CERRADA: si el mismo simbolo
    se recompro despues, "la mas reciente" seria la tesis de la compra
    nueva, no la que realmente causo ese cierre puntual."""
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
    """Ultimo precio conocido por (pool, symbol), de position_marks.jsonl.
    Ese archivo se llena con un precio que el orchestrator YA pedia para
    chequear el stop en cada ciclo -- esto no agrega ninguna llamada nueva
    al broker, solo persiste lo que ya se consultaba y se descartaba."""
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
    """Operaciones REALMENTE cerradas (stop tocado), leidas de los eventos
    `stopped_out` de decisions.log -- no de trades.log, cuyo registro de
    cierre de crypto no trae precio de salida (la respuesta de OKX a una
    orden de venta no incluye fill price, solo un ack). `stopped_out` si
    trae `price` en ambas piernas, y permite juntar la tesis de entrada
    (via `_position_thesis`) con el resultado real.

    Hoy esto vuelve vacio para stocks y crypto: cero cierres reales todavia
    (las unicas ventas en trades.log son de `pool: smoketest`, una prueba
    de conexion, no una posicion real, y se excluyen). Se escribe completo
    para que empiece a poblarse solo apenas ocurra un cierre -- no hace
    falta tocar este archivo ese dia."""
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
    """Envuelve _standings() con una etiqueta honesta: 0 cerradas no es
    'sin datos', es 'el torneo esta corriendo, todavia no vencio el
    horizonte de scoring de 24h para ninguna propuesta'."""
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


# Cache keyed on the mtime of every file build_data() reads. The dashboard
# is polled every 5s but the orchestrator only writes every 15-30min, so
# re-parsing a 100KB+ decisions.log and re-querying tournament.db on every
# single poll was pure waste -- this makes an unchanged tick a dict copy
# instead of a full re-read.
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


_HTML = """<!doctype html>
<html lang="es">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Trading Agent · Command Center</title>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Fira+Code:wght@400;500;600&family=Fira+Sans:wght@400;500;600;700&display=swap">
<style>
  :root {
    --bg: #020617; --card: #0e1223; --card-2: #141a30; --border: #334155;
    --fg: #f8fafc; --muted: #94a3b8;
    /* --accent is the P&L color: green = gaining/bullish/healthy, reused for
       "ok" status. --glow is a separate identity color (console/system-alive,
       navigation chrome), never used for a financial value. --ai marks
       LLM-panel and opportunity-analytics numbers (confidence, opportunity
       score, asymmetry) and nothing else -- never P&L. The three never
       collide in meaning. */
    --accent: #22c55e; --accent-soft: rgba(34,197,94,0.15); --on-accent: #06210f;
    --glow: #22d3ee; --glow-soft: rgba(34,211,238,0.14);
    --ai: #a855f7; --ai-soft: rgba(168,85,247,0.15);
    --warn: #f59e0b; --warn-soft: rgba(245,158,11,0.15);
    --bad: #ef4444; --bad-soft: rgba(239,68,68,0.15);
    --font-body: 'Fira Sans', -apple-system, 'Segoe UI', sans-serif;
    --font-mono: 'Fira Code', ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  }
  @media (prefers-color-scheme: light) {
    :root:not([data-theme="dark"]) {
      --bg: #f8fafc; --card: #ffffff; --card-2: #eef2f7; --border: #dbe2ea;
      --fg: #0f172a; --muted: #64748b;
      --accent: #16a34a; --accent-soft: rgba(22,163,74,0.12); --on-accent: #ffffff;
      --glow: #0891b2; --glow-soft: rgba(8,145,178,0.10);
      --ai: #9333ea; --ai-soft: rgba(147,51,234,0.12);
      --warn: #b45309; --warn-soft: rgba(180,83,9,0.12);
      --bad: #dc2626; --bad-soft: rgba(220,38,38,0.12);
    }
  }
  :root[data-theme="light"] {
    --bg: #f8fafc; --card: #ffffff; --card-2: #eef2f7; --border: #dbe2ea;
    --fg: #0f172a; --muted: #64748b;
    --accent: #16a34a; --accent-soft: rgba(22,163,74,0.12); --on-accent: #ffffff;
    --glow: #0891b2; --glow-soft: rgba(8,145,178,0.10);
    --ai: #9333ea; --ai-soft: rgba(147,51,234,0.12);
    --warn: #b45309; --warn-soft: rgba(180,83,9,0.12);
    --bad: #dc2626; --bad-soft: rgba(220,38,38,0.12);
  }

  * { box-sizing: border-box; }
  html, body { background: var(--bg); }
  body {
    margin: 0; color: var(--fg); background: var(--bg); font-family: var(--font-body);
    padding: 20px clamp(14px, 3.5vw, 40px) 64px; font-size: 14px; line-height: 1.5;
  }
  ::selection { background: var(--accent-soft); }
  a { color: inherit; }
  button { font: inherit; cursor: pointer; }
  :focus-visible { outline: 2px solid var(--glow); outline-offset: 2px; }
  [hidden] { display: none !important; }

  /* ---------- masthead ---------- */
  .masthead {
    display: flex; align-items: center; justify-content: space-between; flex-wrap: wrap; gap: 14px;
    padding-bottom: 12px; margin-bottom: 8px;
  }
  .brand { display: flex; align-items: center; gap: 10px; }
  .brand .pulse-ring { position: relative; width: 10px; height: 10px; flex: none; }
  .brand .pulse-ring::before {
    content: ""; position: absolute; inset: 0; border-radius: 50%; background: var(--glow);
    box-shadow: 0 0 6px 1px var(--glow);
  }
  .brand .pulse-ring::after {
    content: ""; position: absolute; inset: 0; border-radius: 50%; background: var(--glow);
    animation: pulse-ring 2.2s ease-out infinite;
  }
  @media (prefers-reduced-motion: reduce) { .brand .pulse-ring::after { animation: none; opacity: 0; } }
  @keyframes pulse-ring {
    0% { transform: scale(1); opacity: 0.55; }
    100% { transform: scale(2.6); opacity: 0; }
  }
  h1 {
    font-weight: 700; font-size: 18px; margin: 0; letter-spacing: -0.01em;
    text-shadow: 0 0 18px var(--glow-soft);
  }
  .updated { color: var(--muted); font-family: var(--font-mono); font-size: 12px; white-space: nowrap; }

  /* ---------- tab bar ---------- */
  .tabbar {
    display: flex; gap: 2px; overflow-x: auto; border-bottom: 1px solid var(--border);
    margin-bottom: 20px; -ms-overflow-style: none; scrollbar-width: thin;
  }
  .tab-link {
    display: inline-flex; align-items: center; padding: 11px 16px; font-size: 13px; font-weight: 600;
    color: var(--muted); text-decoration: none; border-bottom: 2px solid transparent; white-space: nowrap;
  }
  .tab-link:hover { color: var(--fg); }
  .tab-link.active { color: var(--fg); border-bottom-color: var(--glow); }

  section, .card { margin-bottom: 0; }
  .section-label {
    font-size: 11px; text-transform: uppercase; letter-spacing: 0.08em; color: var(--muted); font-weight: 600;
    margin: 0 0 12px; display: flex; align-items: baseline; justify-content: space-between; gap: 10px;
  }
  .mini-label { font-size: 11px; text-transform: uppercase; letter-spacing: 0.06em; color: var(--muted); font-weight: 600; margin: 0 0 8px; }
  .tab-goto { font-size: 11px; text-transform: none; letter-spacing: normal; color: var(--glow); font-weight: 500; text-decoration: none; }
  .tab-goto:hover { text-decoration: underline; }

  .card { background: var(--card); border: 1px solid var(--border); border-radius: 10px; padding: 16px 18px; }
  .grid-main { display: grid; grid-template-columns: 2.1fr 1fr; gap: 14px; margin-bottom: 14px; align-items: start; }
  .grid-2 { display: grid; grid-template-columns: 1fr 1fr; gap: 14px; margin-bottom: 14px; align-items: start; }
  @media (max-width: 900px) { .grid-main, .grid-2 { grid-template-columns: 1fr; } }
  #tab-content > section:last-child, #tab-content > .grid-2:last-child, #tab-content > .grid-main:last-child { margin-bottom: 0; }

  /* ---------- KPI row ---------- */
  .kpi-row { display: grid; grid-template-columns: repeat(5, 1fr); gap: 12px; margin-bottom: 14px; }
  @media (max-width: 1100px) { .kpi-row { grid-template-columns: repeat(3, 1fr); } }
  @media (max-width: 620px) { .kpi-row { grid-template-columns: repeat(2, 1fr); } }
  .stat-tile { background: var(--card); border: 1px solid var(--border); border-radius: 10px; padding: 14px 16px; }
  .stat-label { font-size: 10px; text-transform: uppercase; letter-spacing: 0.06em; color: var(--muted); font-weight: 600; }
  .stat-value { font-family: var(--font-mono); font-size: 22px; font-weight: 600; margin-top: 6px; letter-spacing: -0.02em; }
  .stat-value.muted { color: var(--muted); font-size: 15px; font-weight: 500; }
  .stat-sub { font-size: 11.5px; color: var(--muted); margin-top: 4px; }
  .stat-sub.pos { color: var(--accent); }
  .stat-sub.neg { color: var(--bad); }

  /* ---------- chart ---------- */
  .chart-head { display: flex; justify-content: space-between; align-items: baseline; margin-bottom: 10px; gap: 10px; flex-wrap: wrap; }
  .chart-head h2 { font-size: 13px; font-weight: 600; margin: 0; }
  .chart-head .now { font-family: var(--font-mono); font-size: 12.5px; color: var(--muted); }
  .chart-head .now b { font-weight: 600; }
  svg.equity-chart { width: 100%; height: auto; display: block; }
  .equity-chart .grid-line { stroke: var(--border); stroke-width: 1; }
  .equity-chart .axis-label { fill: var(--muted); font-family: var(--font-mono); font-size: 10px; }
  .equity-chart .value-line { fill: none; stroke-width: 2; stroke-linejoin: round; stroke-linecap: round; }
  .equity-chart .hover-dot { fill: transparent; }
  .equity-chart .hover-dot:hover { fill: var(--fg); opacity: 0.18; }

  /* ---------- empty states ---------- */
  .empty { color: var(--muted); font-size: 13px; padding: 26px 18px; text-align: center; }
  .empty.small { padding: 14px 10px; font-size: 12px; }
  .empty-sub { font-size: 11.5px; margin-top: 6px; opacity: 0.85; }

  /* ---------- chips ---------- */
  .chip { display: inline-flex; align-items: center; gap: 5px; padding: 3px 9px; border-radius: 6px; font-size: 10.5px; font-weight: 600; text-transform: uppercase; letter-spacing: 0.03em; white-space: nowrap; }
  .chip.ok { background: var(--accent-soft); color: var(--accent); }
  .chip.warn { background: var(--warn-soft); color: var(--warn); }
  .chip.bad { background: var(--bad-soft); color: var(--bad); }
  .chip.muted { background: var(--card-2); color: var(--muted); }
  .chip.ai { background: var(--ai-soft); color: var(--ai); }

  /* ---------- status strip ---------- */
  .status-strip { display: flex; gap: 6px 20px; align-items: center; flex-wrap: wrap; }
  .status-item { display: flex; align-items: center; gap: 6px; font-size: 12.5px; white-space: nowrap; }
  .status-item .who { color: var(--muted); }
  .status-item .when { font-family: var(--font-mono); color: var(--muted); font-size: 11.5px; }
  .status-item .led { width: 7px; height: 7px; border-radius: 50%; flex: none; }
  .status-item .led.ok { background: var(--accent); }
  .status-item .led.warn { background: var(--warn); }
  .status-item .led.bad { background: var(--bad); }

  /* ---------- funnel ---------- */
  .funnel-nums { display: flex; justify-content: space-between; gap: 10px; font-family: var(--font-mono); font-size: 12px; margin-bottom: 8px; flex-wrap: wrap; }
  .funnel-nums .pos { color: var(--accent); }
  .funnel-nums .muted { color: var(--muted); }
  .funnel-track { display: flex; height: 10px; border-radius: 5px; overflow: hidden; background: var(--card-2); margin-bottom: 10px; }
  .funnel-seg.pos { background: var(--accent); }
  .funnel-seg.rej { background: var(--border); }
  .funnel-mini-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 14px; margin-top: 10px; }
  @media (max-width: 560px) { .funnel-mini-grid { grid-template-columns: 1fr; } }

  /* ---------- rejection drill-down ---------- */
  .reason-list { display: flex; flex-wrap: wrap; gap: 8px; }
  .reason-block { display: flex; flex-direction: column; align-items: flex-start; }
  .reason-chip { background: var(--card-2); border: 1px solid var(--border); border-radius: 6px; padding: 6px 10px; font-size: 11px; color: var(--muted); }
  .reason-chip:hover { border-color: var(--muted); color: var(--fg); }
  .reason-chip b { color: var(--fg); font-family: var(--font-mono); font-weight: 600; }
  .reason-symbols { margin-top: 6px; display: flex; flex-wrap: wrap; gap: 5px; max-width: 360px; }
  .sym-chip { background: var(--card-2); border: 1px solid var(--border); border-radius: 4px; padding: 2px 7px; font-size: 10.5px; font-family: var(--font-mono); color: var(--fg); }

  /* ---------- mini rows (overview) ---------- */
  .opp-cols { display: flex; flex-direction: column; gap: 16px; }
  .opp-cols > div:first-child { margin-bottom: 2px; }
  .mini-row { display: grid; grid-template-columns: minmax(76px, auto) auto auto auto; gap: 12px; align-items: center; padding: 6px 0; font-size: 12.5px; border-bottom: 1px solid var(--border); }
  .mini-row .num { min-width: 46px; text-align: right; }
  .mini-row .mono { white-space: nowrap; }
  .mini-row .chip { white-space: nowrap; }
  .mini-row:last-child { border-bottom: none; }
  .mini-row .num { font-family: var(--font-mono); font-variant-numeric: tabular-nums; }

  /* ---------- tables ---------- */
  .table-scroll { overflow-x: auto; }
  table { width: 100%; border-collapse: collapse; min-width: 640px; }
  th, td { text-align: left; padding: 10px 14px; font-size: 12.5px; border-bottom: 1px solid var(--border); white-space: nowrap; }
  td.wrap { white-space: normal; }
  th { color: var(--muted); font-weight: 600; font-size: 10px; text-transform: uppercase; letter-spacing: 0.05em; background: var(--card-2); position: sticky; top: 0; }
  tbody tr:last-child td { border-bottom: none; }
  tbody tr:hover { background: var(--card-2); }
  td.mono { font-family: var(--font-mono); font-variant-numeric: tabular-nums; }
  td.num { text-align: right; }
  .num.pos, .pos { color: var(--accent); }
  .num.neg, .neg { color: var(--bad); }
  .detail { color: var(--muted); }
  .detail.bad { color: var(--bad); }
  .muted { color: var(--muted); }
  .ai-val { color: var(--ai); }

  .result-chip { display: inline-flex; align-items: center; gap: 4px; padding: 2px 8px; border-radius: 6px; font-size: 10.5px; font-weight: 600; white-space: nowrap; }
  .result-chip.ok { background: var(--accent-soft); color: var(--accent); }
  .result-chip.bad { background: var(--bad-soft); color: var(--bad); }
  .result-chip.warn { background: var(--warn-soft); color: var(--warn); }
  .result-chip.muted { background: var(--card-2); color: var(--muted); }

  /* ---------- positions: expandable rows ---------- */
  tr.pos-row { cursor: pointer; }
  tr.pos-row td.chev::before {
    content: "\\25b8"; display: inline-block; width: 12px; margin-right: 4px; color: var(--muted);
    transition: transform 0.15s ease;
  }
  tr.pos-row.expanded td.chev::before { transform: rotate(90deg); }
  tr.detail-row td { background: var(--card-2); white-space: normal; }
  .detail-block { max-width: 900px; padding: 4px 0; }
  .detail-block p { margin: 0 0 6px; font-size: 12.5px; }
  .detail-block p:last-child { margin-bottom: 0; }
  .detail-block .small { font-size: 11.5px; }

  /* ---------- bars / meters (risk) ---------- */
  .bar-list { display: flex; flex-direction: column; gap: 8px; margin-top: 10px; }
  .bar-row { display: grid; grid-template-columns: 110px 1fr auto; gap: 10px; align-items: center; font-size: 12px; }
  .bar-label { color: var(--muted); text-transform: capitalize; }
  .bar-track { height: 8px; background: var(--card-2); border-radius: 4px; overflow: hidden; }
  .bar-fill-flat { height: 100%; border-radius: 4px; background: var(--muted); }
  .bar-value { white-space: nowrap; font-size: 11.5px; }

  .meter { margin-bottom: 12px; }
  .meter-head { display: flex; justify-content: space-between; font-size: 12px; color: var(--muted); margin-bottom: 5px; }
  .meter-track { height: 8px; background: var(--card-2); border-radius: 4px; overflow: hidden; }
  .meter-fill { height: 100%; border-radius: 4px; background: var(--muted); transition: width 0.4s ease; }
  .meter-fill.warn { background: var(--warn); }
  .meter-fill.bad { background: var(--bad); }

  .kv { display: flex; justify-content: space-between; font-size: 12.5px; gap: 10px; padding: 5px 0; }
  .kv .k { color: var(--muted); }
  .kv .v { font-family: var(--font-mono); font-variant-numeric: tabular-nums; }

  /* ---------- alerts ---------- */
  .alert-banner { border-radius: 10px; padding: 14px 18px; border: 1px solid; margin-bottom: 10px; }
  .alert-banner:last-child { margin-bottom: 0; }
  .alert-banner[data-level="warn"] { background: var(--warn-soft); border-color: var(--warn); }
  .alert-banner[data-level="bad"] { background: var(--bad-soft); border-color: var(--bad); }
  .alert-banner[data-level="info"] { background: var(--glow-soft); border-color: var(--glow); }
  .alert-banner .alert-head { font-weight: 700; font-size: 13.5px; margin-bottom: 6px; }
  .alert-banner[data-level="warn"] .alert-head { color: var(--warn); }
  .alert-banner[data-level="bad"] .alert-head { color: var(--bad); }
  .alert-banner[data-level="info"] .alert-head { color: var(--glow); }
  .alert-banner .alert-detail { font-size: 12.5px; color: var(--fg); margin: 0 0 8px; }
  .alert-banner .alert-stats { font-size: 11.5px; color: var(--muted); }

  /* ---------- gauge (panel sentiment) ---------- */
  .gauge-card { display: flex; flex-direction: column; align-items: center; text-align: center; }
  .gauge-card h2 { font-size: 13px; font-weight: 600; margin: 0 0 10px; align-self: flex-start; }
  .gauge-ring { position: relative; width: 104px; height: 104px; }
  .gauge-ring svg { width: 100%; height: 100%; transform: rotate(-90deg); }
  .gauge-ring .track { fill: none; stroke: var(--card-2); stroke-width: 9; }
  .gauge-ring .fill { fill: none; stroke: var(--ai); stroke-width: 9; stroke-linecap: round; }
  .gauge-ring .figure { position: absolute; inset: 0; display: flex; align-items: center; justify-content: center; font-family: var(--font-mono); font-size: 19px; font-weight: 600; }
  .gauge-card .sub { font-size: 11.5px; color: var(--muted); margin-top: 8px; }

  .watchlist-status { display: flex; align-items: center; gap: 12px; margin-bottom: 10px; font-size: 12.5px; }
  .watchlist-status .when { color: var(--muted); font-family: var(--font-mono); font-size: 11.5px; }
  .sym-list { display: flex; flex-wrap: wrap; gap: 6px; }

  @media (max-width: 480px) {
    table { min-width: 540px; }
    .stat-value { font-size: 18px; }
  }
</style>
</head>
<body>
<header class="masthead">
  <div class="brand">
    <span class="pulse-ring" aria-hidden="true"></span>
    <h1>Trading Agent · Command Center</h1>
  </div>
  <span class="updated" id="clock">&mdash;</span>
</header>

<nav class="tabbar" aria-label="Secciones del panel">
  <a href="#overview" class="tab-link" data-tab="overview">Overview</a>
  <a href="#positions" class="tab-link" data-tab="positions">Posiciones</a>
  <a href="#opportunities" class="tab-link" data-tab="opportunities">Oportunidades</a>
  <a href="#activity" class="tab-link" data-tab="activity">Actividad</a>
  <a href="#risk" class="tab-link" data-tab="risk">Riesgo</a>
  <a href="#trades" class="tab-link" data-tab="trades">Trades</a>
  <a href="#strategies" class="tab-link" data-tab="strategies">Estrategias</a>
</nav>

<main id="tab-content">
  <div class="empty">Cargando datos del bot&hellip;</div>
</main>

<script>
const TABS = ["overview","positions","opportunities","activity","risk","trades","strategies"];
const POOL_LABELS = { stocks: "Acciones · IBKR", crypto: "Crypto · OKX" };
const RESULT_CLASS = {
  executed: "ok", smoketest_opened: "ok", smoketest_closed: "ok",
  order_rejected: "bad", order_not_filled: "bad", error: "bad", halted: "bad",
  rejected_by_spend_guard: "bad", stopped_out: "bad",
  skipped: "warn", skipped_sizing: "warn", capital_deployment_alert: "warn",
};

function resultClass(r) { return RESULT_CLASS[r] || "muted"; }

function esc(s) {
  if (s === null || s === undefined) return "";
  return String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

function fmtNum(v, decimals) {
  if (v === null || v === undefined || Number.isNaN(v)) return null;
  const d = decimals === undefined ? 2 : decimals;
  return v.toLocaleString("es-AR", { minimumFractionDigits: d, maximumFractionDigits: d });
}

function fmtMoney(v, decimals) {
  const n = fmtNum(v, decimals === undefined ? 0 : decimals);
  if (n === null) return "\\u2014";
  return (v < 0 ? "-$" : "$") + fmtNum(Math.abs(v), decimals === undefined ? 0 : decimals);
}

function fmtPct(v, decimals) {
  const n = fmtNum(v, decimals === undefined ? 2 : decimals);
  if (n === null) return "\\u2014";
  return (v >= 0 ? "+" : "") + n + "%";
}

function pctClass(v) { return v === null || v === undefined ? "" : v >= 0 ? "pos" : "neg"; }

function fmtAge(mins) {
  if (mins === null || mins === undefined) return "sin datos";
  if (mins < 1) return "hace segundos";
  if (mins < 60) return `hace ${Math.round(mins)} min`;
  return `hace ${fmtNum(mins / 60, 1)} h`;
}

function relTime(iso) {
  if (!iso) return "sin datos";
  const mins = (Date.now() - new Date(iso).getTime()) / 60000;
  return fmtAge(mins);
}

function fmtDateTime(iso) {
  if (!iso) return "\\u2014";
  return iso.replace("T", " ").slice(0, 16) + " UTC";
}

function poolState(h) {
  return h.last_timestamp === null ? "bad" : h.stale ? "warn" : "ok";
}

function statTile(label, value, sub, opts) {
  opts = opts || {};
  return `<div class="stat-tile">
    <div class="stat-label">${label}</div>
    <div class="stat-value${opts.muted ? " muted" : ""}">${value}</div>
    ${sub ? `<div class="stat-sub${opts.subClass ? " " + opts.subClass : ""}">${sub}</div>` : ""}
  </div>`;
}

function toggleRow(tr) {
  const next = tr.nextElementSibling;
  if (!next || !next.classList.contains("detail-row")) return;
  const show = next.hidden;
  next.hidden = !show;
  tr.classList.toggle("expanded", show);
  tr.setAttribute("aria-expanded", String(show));
}

function toggleReason(id) {
  const el = document.getElementById(id);
  if (el) el.hidden = !el.hidden;
}

function reasonChips(reasons, symbolsByReason, idPrefix) {
  const entries = Object.entries(reasons || {}).sort((a, b) => b[1] - a[1]);
  if (!entries.length) return "";
  const blocks = entries.map(([reason, count], i) => {
    const id = `${idPrefix}-${i}`;
    const syms = (symbolsByReason && symbolsByReason[reason]) || [];
    const symsHtml = syms.length
      ? syms.map(s => `<span class="sym-chip">${esc(s)}</span>`).join("")
      : `<span class="muted">sin s\\u00edmbolos registrados</span>`;
    return `<div class="reason-block">
      <button type="button" class="reason-chip" onclick="toggleReason('${id}')" aria-expanded="false">
        <b>${count}</b> ${esc(reason)}
      </button>
      <div id="${id}" class="reason-symbols" hidden>${symsHtml}</div>
    </div>`;
  }).join("");
  return `<div class="reason-list">${blocks}</div>`;
}

function funnelBar(scan) {
  if (!scan) return `<div class="empty small">Sin escaneos todav\\u00eda</div>`;
  const scanned = scan.assets_scanned ?? 0;
  const short = scan.assets_shortlisted ?? 0;
  const rej = scan.assets_rejected ?? Math.max(0, scanned - short);
  const pct = scanned ? (short / scanned * 100) : 0;
  return `<div class="funnel-nums">
      <span>${scanned} escaneados</span>
      <span class="pos">${short} con esperanza +</span>
      <span class="muted">${rej} rechazados</span>
    </div>
    <div class="funnel-track" role="img" aria-label="${short} de ${scanned} activos con esperanza matem\\u00e1tica positiva">
      <div class="funnel-seg pos" style="width:${pct}%"></div>
      <div class="funnel-seg rej" style="width:${100 - pct}%"></div>
    </div>`;
}

function sentimentGauge(s) {
  if (!s) return `<h2>Sentimiento del panel LLM</h2><div class="empty small">Sin decisiones recientes de crypto todav\\u00eda para medirlo.</div>`;
  const r = 40, c = 2 * Math.PI * r;
  const frac = s.buy_pct / 100;
  const mood = s.buy_pct >= 60 ? "alcista" : s.buy_pct <= 15 ? "muy cauto" : "cauto";
  return `<h2>Sentimiento del panel LLM</h2>
    <div class="gauge-ring" role="img" aria-label="${s.buy_pct}% de los votos del panel fueron compra, sobre las \\u00faltimas ${s.sample} decisiones de crypto">
      <svg viewBox="0 0 100 100">
        <circle cx="50" cy="50" r="${r}" class="track"></circle>
        <circle cx="50" cy="50" r="${r}" class="fill" stroke-dasharray="${(frac * c).toFixed(1)} ${c.toFixed(1)}"></circle>
      </svg>
      <span class="figure ai-val">${s.buy_pct}%</span>
    </div>
    <p class="sub">votos de compra · \\u00faltimas ${s.sample} decisiones · ${mood}</p>`;
}

function equityChart(series) {
  if (!series || series.length < 2) {
    const n = series ? series.length : 0;
    return `<div class="chart-head"><h2>Valor del portafolio</h2></div>
      <div class="empty">El historial de rendimiento se est\\u00e1 construyendo &mdash; volv\\u00e9 en unos d\\u00edas.
        <div class="empty-sub">Se necesita m\\u00e1s de una medici\\u00f3n del valor total del portafolio para dibujar una curva; por ahora hay ${n}.</div>
      </div>`;
  }
  const W = 900, H = 220, PAD_L = 72, PAD_R = 14, PAD_T = 16, PAD_B = 26;
  const values = series.map(p => p.total_value);
  const lo = Math.min(...values), hi = Math.max(...values);
  const span = hi - lo;
  const pad = span > 0 ? span * 0.12 : Math.max(1, hi * 0.02);
  const yLo = lo - pad, yHi = hi + pad;
  const xAt = i => PAD_L + (series.length === 1 ? 0 : i / (series.length - 1)) * (W - PAD_L - PAD_R);
  const yAt = v => PAD_T + (1 - (v - yLo) / ((yHi - yLo) || 1)) * (H - PAD_T - PAD_B);
  const rising = values[values.length - 1] >= values[0];
  const lineColor = rising ? "var(--accent)" : "var(--bad)";
  const fillColor = rising ? "var(--accent-soft)" : "var(--bad-soft)";
  const linePath = values.map((v, i) => `${i === 0 ? "M" : "L"} ${xAt(i).toFixed(1)} ${yAt(v).toFixed(1)}`).join(" ");
  const areaPath = `${linePath} L ${xAt(values.length - 1).toFixed(1)} ${(H - PAD_B).toFixed(1)} L ${xAt(0).toFixed(1)} ${(H - PAD_B).toFixed(1)} Z`;
  const dots = series.map((p, i) => `<circle cx="${xAt(i).toFixed(1)}" cy="${yAt(p.total_value).toFixed(1)}" r="9" class="hover-dot"><title>${fmtDateTime(p.timestamp)} \\u2014 ${fmtMoney(p.total_value)}</title></circle>`).join("");
  const totalPct = values[0] ? ((values[values.length - 1] - values[0]) / values[0] * 100) : null;
  return `<div class="chart-head">
      <h2>Valor del portafolio</h2>
      <span class="now">${fmtMoney(values[values.length - 1])} <b class="${rising ? "pos" : "neg"}">${totalPct === null ? "" : fmtPct(totalPct)}</b> · ${series.length} mediciones</span>
    </div>
    <svg viewBox="0 0 ${W} ${H}" class="equity-chart" role="img" aria-label="Curva de valor del portafolio, de ${fmtMoney(values[0])} a ${fmtMoney(values[values.length - 1])} en ${series.length} mediciones">
      <line x1="${PAD_L}" y1="${(H - PAD_B).toFixed(1)}" x2="${W - PAD_R}" y2="${(H - PAD_B).toFixed(1)}" class="grid-line"></line>
      <text x="${PAD_L - 8}" y="${PAD_T + 4}" text-anchor="end" class="axis-label">${fmtMoney(yHi, 0)}</text>
      <text x="${PAD_L - 8}" y="${H - PAD_B + 4}" text-anchor="end" class="axis-label">${fmtMoney(yLo, 0)}</text>
      <path d="${areaPath}" style="fill:${fillColor}" stroke="none"></path>
      <path d="${linePath}" class="value-line" style="stroke:${lineColor}"></path>
      ${dots}
      <text x="${PAD_L}" y="${H - 4}" class="axis-label">${fmtDateTime(series[0].timestamp)}</text>
      <text x="${W - PAD_R}" y="${H - 4}" text-anchor="end" class="axis-label">${fmtDateTime(series[series.length - 1].timestamp)}</text>
    </svg>`;
}

function pnlToday(series) {
  if (!series || series.length < 2) return null;
  const lastTs = new Date(series[series.length - 1].timestamp).getTime();
  const firstTs = new Date(series[0].timestamp).getTime();
  if (lastTs - firstTs < 18 * 3600 * 1000) return null;
  const target = lastTs - 24 * 3600 * 1000;
  let best = series[0], bestDiff = Infinity;
  for (const p of series) {
    const diff = Math.abs(new Date(p.timestamp).getTime() - target);
    if (diff < bestDiff) { bestDiff = diff; best = p; }
  }
  const latest = series[series.length - 1].total_value;
  const usd = latest - best.total_value;
  const pct = best.total_value ? (usd / best.total_value * 100) : null;
  return { usd, pct };
}

function deploymentAlertBanner(pool, a) {
  const bad = a.diagnosis === "SYSTEM_FAILED_TO_DEPLOY";
  const title = bad
    ? "Fallo del sistema: no despleg\\u00f3 capital pese a haber oportunidades"
    : "Sin oportunidades viables en el \\u00faltimo escaneo";
  return `<div class="alert-banner" data-level="${bad ? "bad" : "warn"}">
    <div class="alert-head">${POOL_LABELS[pool]} · ${title}</div>
    <p class="alert-detail">${esc(a.detail)}</p>
    <div class="alert-stats">${a.assets_scanned ?? "\\u2014"} escaneados · ${a.shortlisted ?? "\\u2014"} con esperanza positiva · ${fmtAge(a.age_minutes)}</div>
  </div>`;
}

function riskAlertBanner(a) {
  return `<div class="alert-banner" data-level="${a.level === "warning" ? "warn" : "info"}">
    <div class="alert-head">${esc(a.code)}</div>
    <p class="alert-detail">${esc(a.detail)}</p>
  </div>`;
}

/* ================= OVERVIEW ================= */
function overviewTab(d, el) {
  const p = d.portfolio;
  const cash = (p.total_capital != null && p.deployed_capital != null) ? p.total_capital - p.deployed_capital : null;
  const pnl = pnlToday(d.portfolio_series);

  const kpis = [
    statTile("Valor del portafolio", fmtMoney(p.total_capital)),
    pnl
      ? statTile("P&L de hoy", fmtMoney(pnl.usd), pnl.pct === null ? "" : fmtPct(pnl.pct), { subClass: pctClass(pnl.usd) })
      : statTile("P&L de hoy", "N/A", "Sin hist\\u00f3rico suficiente todav\\u00eda \\u2014 se est\\u00e1 registrando desde ahora", { muted: true }),
    statTile("Cash", fmtMoney(cash), p.cash_pct != null ? `${fmtNum(p.cash_pct, 1)}% del capital` : ""),
    statTile("Invertido", fmtMoney(p.deployed_capital)),
    statTile("Exposici\\u00f3n", d.risk.exposure_pct != null ? `${fmtNum(d.risk.exposure_pct, 1)}%` : "\\u2014", `${p.n_positions} ${p.n_positions === 1 ? "posici\\u00f3n" : "posiciones"}`),
  ].join("");

  const topOpp = ["stocks", "crypto"].map(pool => {
    const disc = d.discovery[pool];
    if (!disc) return `<div><p class="mini-label">${POOL_LABELS[pool]}</p><div class="empty small">sin escaneos todav\\u00eda</div></div>`;
    const list = (disc.top_candidates || []).slice(0, 5);
    const rows = list.length ? list.map(c => {
      const open = d.live_positions.some(lp => lp.pool === pool && lp.symbol === c.symbol);
      return `<div class="mini-row">
        <span class="mono">${esc(c.symbol)}</span>
        <span class="num">${fmtNum(c.score, 1)}</span>
        <span class="num ${pctClass(c.ev_pct)}">${fmtPct(c.ev_pct)}</span>
        <span>${open ? '<span class="chip ok">EN POSICI\\u00d3N</span>' : '<span class="chip muted">OBSERVANDO</span>'}</span>
      </div>`;
    }).join("") : `<div class="empty small">sin candidatos en el \\u00faltimo escaneo</div>`;
    return `<div><p class="mini-label">${POOL_LABELS[pool]}</p>${rows}</div>`;
  }).join("");

  const posRows = d.live_positions.slice(0, 5).map(lp => `
    <div class="mini-row">
      <span class="mono">${esc(lp.symbol)}</span>
      <span class="num">${fmtMoney(lp.market_value)}</span>
      <span class="num ${pctClass(lp.pnl_pct)}">${lp.pnl_pct === null ? "\\u2014" : fmtPct(lp.pnl_pct)}</span>
      <span></span>
    </div>`).join("") || `<div class="empty small">No hay posiciones abiertas.</div>`;

  const funnelsMini = ["stocks", "crypto"].map(pool => `
    <div><p class="mini-label">${POOL_LABELS[pool]}</p>${funnelBar(d.discovery[pool])}</div>`).join("");

  const poolStatusMini = ["stocks", "crypto"].map(pool => {
    const h = d.pools[pool];
    return `<div class="status-item"><span class="led ${poolState(h)}"></span><span class="who">${POOL_LABELS[pool]}</span><span class="when">${fmtAge(h.age_minutes)}</span></div>`;
  }).join("");

  const alertBanners = [];
  for (const pool of ["stocks", "crypto"]) {
    const a = d.deployment_alerts[pool];
    if (a) alertBanners.push(deploymentAlertBanner(pool, a));
  }
  for (const a of (d.risk.alerts || [])) alertBanners.push(riskAlertBanner(a));
  const alertsHTML = alertBanners.length ? alertBanners.join("") : `<div class="empty small">Sin alertas activas.</div>`;

  el.innerHTML = `
    <section class="kpi-row">${kpis}</section>
    <section class="grid-main">
      <div class="card">${equityChart(d.portfolio_series)}</div>
      <div class="card">
        <div class="section-label">Top oportunidades ahora <a class="tab-goto" href="#opportunities">ver todas &rarr;</a></div>
        <div class="opp-cols">${topOpp}</div>
      </div>
    </section>
    <section class="grid-main">
      <div class="card">
        <div class="section-label">Posiciones abiertas <a class="tab-goto" href="#positions">ver todas &rarr;</a></div>
        ${posRows}
      </div>
      <div class="card">
        <div class="section-label">Actividad del bot <a class="tab-goto" href="#activity">ver detalle &rarr;</a></div>
        <div class="status-strip">${poolStatusMini}</div>
        <div class="funnel-mini-grid">${funnelsMini}</div>
      </div>
    </section>
    <section class="card">
      <div class="section-label">Alertas</div>
      ${alertsHTML}
    </section>`;
}

/* ================= POSITIONS ================= */
function positionDetail(p) {
  const reasoning = p.reasoning
    ? `<p>${esc(p.reasoning)}</p>`
    : `<p class="muted">Sin tesis registrada para esta posici\\u00f3n.</p>`;
  let asym;
  if (p.asymmetry) {
    const a = p.asymmetry;
    asym = `<p class="ai-val">Objetivo +${fmtNum(a.target_pct, 1)}% / Stop -${fmtNum(Math.abs(a.stop_pct), 1)}% \\u2014 R:R ${fmtNum(a.reward_risk, 2)} \\u2014 probabilidad de acierto medida ${fmtNum(a.win_prob * 100, 0)}% sobre ${a.sample_size} ventanas hist\\u00f3ricas (${fmtNum(a.resolved_pct, 1)}% resueltas)</p>
      <p class="small muted">EV esperado ${fmtPct(a.expected_value_pct)} · volatilidad ${fmtNum(a.volatility_pct, 2)}%${p.opportunity_score != null ? ` · score de oportunidad <span class="ai-val">${fmtNum(p.opportunity_score, 1)}</span>` : ""}</p>`;
  } else {
    asym = `<p class="muted">Esta posici\\u00f3n es de antes del sistema de asimetr\\u00eda \\u2014 no tiene ese dato registrado.</p>`;
  }
  return `<div class="detail-block">${reasoning}${asym}</div>`;
}

function positionsTab(d, el) {
  const rows = d.live_positions;
  if (!rows.length) {
    el.innerHTML = `<section class="card"><div class="section-label">Posiciones abiertas</div><div class="empty">No hay posiciones abiertas en este momento.</div></section>`;
    return;
  }
  const trs = rows.map(p => {
    const noMark = `<span class="muted" title="Sin marca de precio reciente para esta posici\\u00f3n">\\u2014</span>`;
    const current = p.current_price === null ? noMark : fmtMoney(p.current_price, 2);
    const pnlUsd = p.pnl_usd === null ? noMark : `<span class="${pctClass(p.pnl_usd)}">${fmtMoney(p.pnl_usd)}</span>`;
    const pnlPct = p.pnl_pct === null ? noMark : `<span class="${pctClass(p.pnl_pct)}">${fmtPct(p.pnl_pct)}</span>`;
    const conf = (p.confidence === null || p.confidence === undefined) ? "\\u2014" : `<span class="ai-val">${fmtNum(p.confidence * 100, 0)}%</span>`;
    return `<tr class="pos-row" tabindex="0" role="button" aria-expanded="false" onclick="toggleRow(this)" onkeydown="if(event.key==='Enter'||event.key===' '){event.preventDefault();toggleRow(this);}">
      <td class="mono chev">${esc(p.symbol)}</td>
      <td>${POOL_LABELS[p.pool] || p.pool}</td>
      <td class="mono num">${fmtMoney(p.entry_price, 2)}</td>
      <td class="mono num">${current}</td>
      <td class="mono num">${p.qty}</td>
      <td class="mono num">${fmtMoney(p.market_value)}</td>
      <td class="mono num">${pnlUsd}</td>
      <td class="mono num">${pnlPct}</td>
      <td class="mono num">${fmtMoney(p.stop, 2)}</td>
      <td class="mono num">${fmtMoney(p.risk_usd)}</td>
      <td>${p.bucket ? `<span class="chip muted">${esc(p.bucket)}</span>` : "\\u2014"}</td>
      <td class="mono num">${conf}</td>
      <td class="mono">${relTime(p.opened_at)}</td>
      <td class="wrap">${esc(p.strategy) || "\\u2014"}</td>
    </tr>
    <tr class="detail-row" hidden><td colspan="14">${positionDetail(p)}</td></tr>`;
  }).join("");
  el.innerHTML = `<section class="card table-scroll">
    <table>
      <thead><tr>
        <th>S\\u00edmbolo</th><th>Pool</th><th>Entrada</th><th>Actual</th><th>Cant.</th><th>Valor mercado</th>
        <th>P&amp;L $</th><th>P&amp;L %</th><th>Stop</th><th>Riesgo $</th><th>Bucket</th><th>Confianza</th><th>Abierta</th><th>Estrategia</th>
      </tr></thead>
      <tbody>${trs}</tbody>
    </table>
  </section>`;
}

/* ================= OPPORTUNITIES ================= */
function opportunitiesTab(d, el) {
  const sections = ["stocks", "crypto"].map(pool => {
    const disc = d.discovery[pool];
    if (!disc) {
      return `<section class="card"><div class="section-label">${POOL_LABELS[pool]}</div><div class="empty">Sin escaneos todav\\u00eda para este pool.</div></section>`;
    }
    const list = disc.top_candidates || [];
    const rows = list.map(c => {
      const open = d.live_positions.some(lp => lp.pool === pool && lp.symbol === c.symbol);
      return `<tr>
        <td class="mono">${esc(c.symbol)}</td>
        <td class="mono num">${fmtNum(c.score, 2)}</td>
        <td class="mono num ${pctClass(c.ev_pct)}">${fmtPct(c.ev_pct)}</td>
        <td>${open ? '<span class="chip ok">EN POSICI\\u00d3N</span>' : '<span class="chip muted">OBSERVANDO</span>'}</td>
      </tr>`;
    }).join("");
    const table = rows
      ? `<div class="table-scroll"><table><thead><tr><th>S\\u00edmbolo</th><th>Score</th><th>EV%</th><th>Estado</th></tr></thead><tbody>${rows}</tbody></table></div>`
      : `<div class="empty small">Sin candidatos con esperanza positiva en el \\u00faltimo escaneo.</div>`;
    const chips = reasonChips(disc.rejection_reasons, disc.rejected_symbols, `rej-${pool}`);
    return `<section class="card">
      <div class="section-label">${POOL_LABELS[pool]} <span class="muted" style="text-transform:none;letter-spacing:normal;font-weight:400">${disc.assets_scanned} escaneados</span></div>
      ${table}
      ${chips ? `<div style="margin-top:14px"><p class="mini-label">Por qu\\u00e9 el bot descart\\u00f3 el resto</p>${chips}</div>` : ""}
    </section>`;
  }).join("");
  el.innerHTML = `<div class="grid-2">${sections}</div>`;
}

/* ================= ACTIVITY ================= */
function decisionsFeed(rows) {
  if (!rows.length) return `<div class="empty">Todav\\u00eda no hay decisiones registradas.</div>`;
  const trs = rows.map(r => {
    const symbol = (r.signal && r.signal.symbol) || r.symbol || "\\u2014";
    const detail = (r.review && r.review.reasoning) || r.detail || r.reason || "";
    return `<tr>
      <td class="mono">${fmtDateTime(r.timestamp)}</td>
      <td>${POOL_LABELS[r.pool] || r.pool || "\\u2014"}</td>
      <td class="mono">${esc(symbol)}</td>
      <td><span class="result-chip ${resultClass(r.result)}">${esc(r.result)}</span></td>
      <td class="wrap detail">${esc(detail)}</td>
    </tr>`;
  }).join("");
  return `<div class="table-scroll"><table><thead><tr><th>Hora (UTC)</th><th>Pool</th><th>S\\u00edmbolo</th><th>Resultado</th><th>Detalle</th></tr></thead><tbody>${trs}</tbody></table></div>`;
}

function watchlistBlock(w) {
  const chip = w.last_error
    ? '<span class="chip bad">actualizaci\\u00f3n fall\\u00f3</span>'
    : w.stale
      ? '<span class="chip warn">desactualizada</span>'
      : '<span class="chip ok">al d\\u00eda</span>';
  const when = w.last_updated ? `actualizada ${fmtAge(w.age_minutes)}` : "nunca se corri\\u00f3 la actualizaci\\u00f3n autom\\u00e1tica";
  const symbols = (w.symbols && w.symbols.length)
    ? `<div class="sym-list">${w.symbols.map(s => `<span class="sym-chip">${esc(s)}</span>`).join("")}</div>`
    : `<div class="empty small">Sin watchlist todav\\u00eda.</div>`;
  return `<div class="card">
    <div class="section-label">Watchlist</div>
    <div class="watchlist-status">${chip}<span class="when">${when}</span></div>
    ${symbols}
    ${w.last_error ? `<p class="detail bad" style="margin-top:8px">${esc(w.last_error)}</p>` : ""}
  </div>`;
}

function activityTab(d, el) {
  const funnels = ["stocks", "crypto"].map(pool => `
    <div class="card">
      <div class="section-label">${POOL_LABELS[pool]}</div>
      ${funnelBar(d.discovery[pool])}
      ${d.discovery[pool] ? reasonChips(d.discovery[pool].rejection_reasons, d.discovery[pool].rejected_symbols, `act-${pool}`) : ""}
    </div>`).join("");

  const totalsChips = reasonChips(d.rejection_totals, null, "act-total");

  const poolStatus = ["stocks", "crypto"].map(pool => {
    const h = d.pools[pool];
    return `<div class="status-item"><span class="led ${poolState(h)}"></span><span class="who">${POOL_LABELS[pool]}</span><span class="when">${fmtAge(h.age_minutes)} · ${h.last_result ? esc(h.last_result) : "\\u2014"}</span></div>`;
  }).join("");

  el.innerHTML = `
    <div class="grid-2">${funnels}</div>
    <section class="card">
      <div class="section-label">Motivos de rechazo combinados (acciones + crypto)</div>
      ${totalsChips || '<div class="empty small">Sin rechazos registrados.</div>'}
    </section>
    <div class="grid-2">
      <section class="card">
        <div class="section-label">Estado de los pools</div>
        <div class="status-strip">${poolStatus}</div>
      </section>
      ${watchlistBlock(d.watchlist)}
    </div>
    <section class="card gauge-card">${sentimentGauge(d.panel_sentiment)}</section>
    <section class="card">
      <div class="section-label">Decisiones recientes</div>
      ${decisionsFeed(d.recent_decisions)}
    </section>`;
}

/* ================= RISK ================= */
function riskTab(d, el) {
  const r = d.risk;

  const exposureRows = ["stocks", "crypto"].map(pool => {
    const v = r.exposure_by_pool[pool] || 0;
    const pct = r.total_capital ? (v / r.total_capital * 100) : 0;
    return `<div class="bar-row"><span class="bar-label">${POOL_LABELS[pool]}</span><div class="bar-track"><div class="bar-fill-flat" style="width:${pct}%"></div></div><span class="bar-value mono">${fmtMoney(v)} · ${fmtNum(pct, 1)}%</span></div>`;
  }).join("");

  const bucketEntries = Object.entries(r.bucket_exposure || {});
  const bucketRows = bucketEntries.length ? bucketEntries.map(([b, v]) => {
    const pct = r.total_capital ? (v / r.total_capital * 100) : 0;
    return `<div class="bar-row"><span class="bar-label">${esc(b)}</span><div class="bar-track"><div class="bar-fill-flat" style="width:${pct}%"></div></div><span class="bar-value mono">${fmtMoney(v)} · ${fmtNum(pct, 1)}%</span></div>`;
  }).join("") : `<div class="empty small">Sin exposici\\u00f3n por bucket.</div>`;

  const posMeterPct = r.max_open_positions ? Math.min(100, r.n_positions / r.max_open_positions * 100) : 0;
  const expMeterPct = (r.exposure_pct != null && r.max_deployed_pct) ? Math.min(100, r.exposure_pct / r.max_deployed_pct * 100) : 0;
  const meterCls = pct => pct >= 90 ? " bad" : pct >= 70 ? " warn" : "";

  const spendRows = ["stocks", "crypto"].map(pool => {
    const s = r.spend_today[pool];
    return `<div class="kv"><span class="k">${POOL_LABELS[pool]}</span><span class="v">${s ? fmtMoney(s.spent) : "\\u2014"}</span></div>`;
  }).join("");

  const breakerCards = ["stocks", "crypto"].map(pool => {
    const b = d.breakers[pool];
    if (!b) return `<div class="card"><div class="section-label">${POOL_LABELS[pool]} · circuit breaker</div><div class="empty small">Sin datos de circuit breaker.</div></div>`;
    return `<div class="card">
      <div class="section-label">${POOL_LABELS[pool]} · circuit breaker</div>
      <div class="kv"><span class="k">Estado</span><span class="v ${b.halted ? "neg" : "pos"}">${b.halted ? "DETENIDO" : "activo"}</span></div>
      ${b.halted ? `<div class="kv"><span class="k">Motivo</span><span class="v">${esc(b.halt_reason) || "\\u2014"}</span></div>` : ""}
      <div class="kv"><span class="k">P\\u00e9rdidas seguidas</span><span class="v">${b.consecutive_losses}</span></div>
      <div class="kv"><span class="k">Valor al inicio del d\\u00eda</span><span class="v">${fmtMoney(b.day_start_value)}</span></div>
    </div>`;
  }).join("");

  const alertsHTML = (r.alerts || []).length ? r.alerts.map(riskAlertBanner).join("") : `<div class="empty small">Sin alertas de riesgo activas.</div>`;

  el.innerHTML = `
    <div class="grid-2">
      <section class="card">
        <div class="section-label">Exposici\\u00f3n total</div>
        <div class="stat-value">${fmtMoney(r.total_capital)}</div>
        <div class="stat-sub">${r.exposure_pct != null ? fmtNum(r.exposure_pct, 1) : "\\u2014"}% desplegado</div>
        <div class="bar-list">${exposureRows}</div>
      </section>
      <section class="card">
        <div class="section-label">Exposici\\u00f3n por bucket</div>
        <div class="bar-list">${bucketRows}</div>
      </section>
    </div>
    <div class="grid-2">
      <section class="card">
        <div class="section-label">Topes operativos</div>
        <div class="meter">
          <div class="meter-head"><span>Posiciones abiertas</span><span class="mono">${r.n_positions} / ${r.max_open_positions}</span></div>
          <div class="meter-track"><div class="meter-fill${meterCls(posMeterPct)}" style="width:${posMeterPct}%"></div></div>
        </div>
        <div class="meter">
          <div class="meter-head"><span>Capital desplegado</span><span class="mono">${r.exposure_pct != null ? fmtNum(r.exposure_pct, 1) : "\\u2014"}% / ${fmtNum(r.max_deployed_pct, 0)}%</span></div>
          <div class="meter-track"><div class="meter-fill${meterCls(expMeterPct)}" style="width:${expMeterPct}%"></div></div>
        </div>
        <div class="kv"><span class="k">Posici\\u00f3n m\\u00e1s grande</span><span class="v">${r.largest_position ? `${esc(r.largest_position.symbol)} · ${fmtMoney(r.largest_position.market_value)}` : "\\u2014"}</span></div>
        <div class="kv"><span class="k">Riesgo abierto total</span><span class="v">${fmtMoney(r.open_risk_usd)}</span></div>
        <p class="detail" style="margin-top:6px">Riesgo abierto: lo que se pierde si TODOS los stops se tocan a la vez.</p>
      </section>
      <section class="card">
        <div class="section-label">Gasto de hoy por pool</div>
        ${spendRows}
      </section>
    </div>
    <div class="grid-2">${breakerCards}</div>
    <section class="card">
      <div class="section-label">Alertas de riesgo</div>
      ${alertsHTML}
    </section>`;
}

/* ================= TRADES ================= */
function tradesTab(d, el) {
  const trades = d.closed_trades;
  const n = d.portfolio.n_positions;
  if (!trades.length) {
    el.innerHTML = `<section class="card">
      <div class="section-label">Operaciones cerradas</div>
      <div class="empty">0 operaciones cerradas &mdash; ${n} ${n === 1 ? "posici\\u00f3n" : "posiciones"} abierta${n === 1 ? "" : "s"} actualmente, ninguna alcanz\\u00f3 su stop todav\\u00eda.</div>
    </section>`;
    return;
  }
  const trs = trades.map(t => `<tr>
    <td class="mono">${esc(t.symbol)}</td>
    <td>${POOL_LABELS[t.pool] || t.pool}</td>
    <td>${t.bucket ? `<span class="chip muted">${esc(t.bucket)}</span>` : "\\u2014"}</td>
    <td class="mono num">${fmtMoney(t.exit_price, 2)}</td>
    <td class="mono num">${fmtMoney(t.stop, 2)}</td>
    <td>${t.won ? '<span class="chip ok">GANADA</span>' : '<span class="chip bad">PERDIDA</span>'}</td>
    <td class="mono num">${fmtMoney(t.position_usd)}</td>
    <td class="mono">${relTime(t.opened_at)}</td>
    <td class="mono">${relTime(t.closed_at)}</td>
    <td class="wrap detail">${esc(t.reasoning) || "\\u2014"}</td>
  </tr>`).join("");
  el.innerHTML = `<section class="card table-scroll">
    <table>
      <thead><tr><th>S\\u00edmbolo</th><th>Pool</th><th>Bucket</th><th>Salida</th><th>Stop</th><th>Resultado</th><th>Monto</th><th>Apertura</th><th>Cierre</th><th>Tesis</th></tr></thead>
      <tbody>${trs}</tbody>
    </table>
  </section>`;
}

/* ================= STRATEGIES ================= */
function strategiesTab(d, el) {
  const rows = d.strategy_performance;
  if (!rows.length) {
    el.innerHTML = `<section class="card"><div class="section-label">Torneo de estrategias</div><div class="empty">El torneo todav\\u00eda no registr\\u00f3 propuestas.</div></section>`;
    return;
  }
  const trs = rows.map(s => {
    if (s.status === "awaiting_results") {
      return `<tr>
        <td>${esc(s.strategy)}</td>
        <td class="mono num">${s.open_proposals}</td>
        <td class="mono num">${s.scored}</td>
        <td colspan="4" class="muted">N/A &mdash; esperando resultados (${s.open_proposals} propuesta${s.open_proposals === 1 ? "" : "s"} abierta${s.open_proposals === 1 ? "" : "s"}, todav\\u00eda no vencieron las 24h de scoring)</td>
      </tr>`;
    }
    return `<tr>
      <td>${esc(s.strategy)}</td>
      <td class="mono num">${s.open_proposals}</td>
      <td class="mono num">${s.scored}</td>
      <td class="mono num ${pctClass(s.win_rate)}">${s.win_rate != null ? fmtNum(s.win_rate, 1) + "%" : "\\u2014"}</td>
      <td class="mono num ${pctClass(s.avg_return_pct)}">${fmtPct(s.avg_return_pct)}</td>
      <td class="mono num ${pctClass(s.best_pct)}">${fmtPct(s.best_pct)}</td>
      <td class="mono num ${pctClass(s.worst_pct)}">${fmtPct(s.worst_pct)}</td>
    </tr>`;
  }).join("");
  el.innerHTML = `<section class="card table-scroll">
    <table>
      <thead><tr><th>Estrategia</th><th>Abiertas</th><th>Cerradas</th><th>Win rate</th><th>Retorno prom.</th><th>Mejor</th><th>Peor</th></tr></thead>
      <tbody>${trs}</tbody>
    </table>
  </section>`;
}

/* ================= router / polling ================= */
const RENDERERS = {
  overview: overviewTab, positions: positionsTab, opportunities: opportunitiesTab,
  activity: activityTab, risk: riskTab, trades: tradesTab, strategies: strategiesTab,
};

const state = { tab: null, data: null, sig: null, renderedSig: null };

function currentTab() {
  const h = (location.hash || "").replace("#", "");
  return TABS.includes(h) ? h : "overview";
}

function renderRoute() {
  const tab = currentTab();
  if (tab === state.tab && state.sig === state.renderedSig) return;
  document.querySelectorAll(".tab-link").forEach(a => a.classList.toggle("active", a.dataset.tab === tab));
  const el = document.getElementById("tab-content");
  if (state.data) {
    try {
      RENDERERS[tab](state.data, el);
    } catch (err) {
      el.innerHTML = `<div class="empty">Error mostrando esta pesta\\u00f1a: ${esc(err.message)}</div>`;
      console.error(err);
    }
  }
  state.tab = tab;
  state.renderedSig = state.sig;
}

async function refresh() {
  let d;
  try {
    const res = await fetch("/data");
    d = await res.json();
  } catch (err) {
    console.error(err);
    return;
  }
  state.data = d;
  const rest = Object.assign({}, d);
  delete rest.generated_at;
  state.sig = JSON.stringify(rest);
  document.getElementById("clock").textContent = "actualizado " + new Date(d.generated_at).toLocaleTimeString("es-AR");
  renderRoute();
}

window.addEventListener("hashchange", renderRoute);
refresh();
setInterval(refresh, 5000);
</script>
</body>
</html>
"""


class Handler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass  # keep the terminal quiet

    def _send(self, status: int, content_type: str, body: bytes) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path in ("/", "/index.html"):
            self._send(200, "text/html; charset=utf-8", _HTML.encode("utf-8"))
        elif self.path == "/data":
            self._send(200, "application/json", json.dumps(build_data()).encode("utf-8"))
        else:
            self._send(404, "text/plain", b"not found")


def main() -> None:
    server = HTTPServer(("127.0.0.1", PORT), Handler)
    print(f"Dashboard: http://127.0.0.1:{PORT}  (Ctrl+C to stop, or just close this window)")
    webbrowser.open(f"http://127.0.0.1:{PORT}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
