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


def build_data() -> dict:
    decisions = _tail_jsonl(LOGS_DIR / "decisions.log", 500)
    trades = _tail_jsonl(LOGS_DIR / "trades.log", 20)
    errors = [e for e in decisions if e.get("result") == "error" and "timestamp" in e and _age_minutes(e["timestamp"]) < 24 * 60]
    # Old, already-resolved log lines (e.g. pre-fix connection errors from
    # days ago) shouldn't pad out "recent" once today's activity is thin --
    # that reads as something currently wrong when it's just history.
    recent = [d for d in decisions if "timestamp" in d and _age_minutes(d["timestamp"]) < 48 * 60]

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "pools": {pool: _pool_health(decisions, pool) for pool in ("stocks", "crypto")},
        "positions": {p: v for p in ("stocks", "crypto") if (v := _read_json(STATE_DIR / f"positions_{p}.json"))},
        "breakers": {p: v for p in ("stocks", "crypto") if (v := _read_json(STATE_DIR / f"breaker_{p}.json"))},
        "spend": {p: v for p in ("stocks", "crypto") if (v := _read_json(STATE_DIR / f"spend_{p}.json"))},
        "error_count": len(errors),
        "last_error": errors[-1] if errors else None,
        "recent_decisions": list(reversed(recent))[:30],
        "recent_trades": list(reversed(trades)),
    }


_HTML = """<!doctype html>
<html lang="es">
<head>
<meta charset="utf-8">
<title>Trading Agent Dashboard</title>
<style>
  :root {
    --bg: #0b0f14; --panel: #121821; --panel-2: #171f2b; --border: #232d3b;
    --text: #e6edf5; --muted: #8a97a8; --accent: #5aa0ff;
    --ok: #3ecf8e; --warn: #e8b93f; --bad: #ef5a6f;
    --mono: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  }
  * { box-sizing: border-box; }
  body {
    margin: 0; background: var(--bg); color: var(--text);
    font-family: -apple-system, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
    padding: 24px 32px 64px;
  }
  header { display: flex; align-items: baseline; justify-content: space-between; margin-bottom: 20px; }
  h1 { font-size: 19px; font-weight: 600; margin: 0; letter-spacing: 0.2px; }
  #clock { color: var(--muted); font-size: 12px; font-family: var(--mono); }
  .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 14px; margin-bottom: 24px; }
  .card {
    background: var(--panel); border: 1px solid var(--border); border-radius: 10px;
    padding: 16px 18px;
  }
  .card h2 { font-size: 12px; text-transform: uppercase; letter-spacing: 0.6px; color: var(--muted); margin: 0 0 10px; font-weight: 600; }
  .pill { display: inline-flex; align-items: center; gap: 6px; padding: 3px 10px; border-radius: 999px; font-size: 12px; font-weight: 600; }
  .pill.ok { background: rgba(62,207,142,0.14); color: var(--ok); }
  .pill.warn { background: rgba(232,185,63,0.14); color: var(--warn); }
  .pill.bad { background: rgba(239,90,111,0.14); color: var(--bad); }
  .dot { width: 7px; height: 7px; border-radius: 50%; background: currentColor; }
  .row { display: flex; justify-content: space-between; align-items: center; margin-top: 8px; font-size: 13px; }
  .row .label { color: var(--muted); }
  .mono { font-family: var(--mono); font-size: 12.5px; }
  section { margin-bottom: 26px; }
  section > h2 { font-size: 13px; text-transform: uppercase; letter-spacing: 0.6px; color: var(--muted); margin: 0 0 10px; }
  table { width: 100%; border-collapse: collapse; background: var(--panel); border: 1px solid var(--border); border-radius: 10px; overflow: hidden; }
  th, td { text-align: left; padding: 9px 14px; font-size: 12.5px; border-bottom: 1px solid var(--border); }
  th { color: var(--muted); font-weight: 600; font-size: 11px; text-transform: uppercase; background: var(--panel-2); }
  tr:last-child td { border-bottom: none; }
  td.result-executed { color: var(--ok); font-weight: 600; }
  td.result-error { color: var(--bad); font-weight: 600; }
  td.result-skipped, td.result-no_signals, td.result-market_closed, td.result-skipped_indicators { color: var(--muted); }
  .empty { color: var(--muted); font-size: 13px; padding: 18px; text-align: center; }
  details { background: var(--panel); border: 1px solid var(--border); border-radius: 10px; padding: 12px 16px; }
  summary { cursor: pointer; font-size: 13px; color: var(--bad); font-weight: 600; }
  pre { white-space: pre-wrap; word-break: break-word; font-size: 11.5px; color: var(--muted); margin: 10px 0 0; max-height: 320px; overflow-y: auto; }
</style>
</head>
<body>
<header>
  <h1>Trading Agent -- panel de estado (local, solo lectura)</h1>
  <span id="clock">--</span>
</header>

<div class="grid" id="pool-cards"></div>
<div class="grid" id="breaker-cards"></div>

<section>
  <h2>Decisiones recientes</h2>
  <div id="decisions-wrap"></div>
</section>

<section>
  <h2>Ordenes ejecutadas</h2>
  <div id="trades-wrap"></div>
</section>

<section id="errors-section" hidden>
  <h2>Errores</h2>
  <details>
    <summary id="errors-summary"></summary>
    <pre id="error-trace"></pre>
  </details>
</section>

<script>
function fmtAge(mins) {
  if (mins === null || mins === undefined) return "sin datos";
  if (mins < 1) return "hace segundos";
  if (mins < 60) return `hace ${Math.round(mins)} min`;
  return `hace ${(mins / 60).toFixed(1)} h`;
}

function poolCard(name, label, h) {
  const status = h.last_timestamp === null
    ? { cls: "bad", text: "sin corridas" }
    : h.stale
      ? { cls: "bad", text: "atrasado" }
      : { cls: "ok", text: "al dia" };
  return `
    <div class="card">
      <h2>${label}</h2>
      <span class="pill ${status.cls}"><span class="dot"></span>${status.text}</span>
      <div class="row"><span class="label">Ultima corrida</span><span>${fmtAge(h.age_minutes)}</span></div>
      <div class="row"><span class="label">Resultado</span><span class="mono">${h.last_result ?? "--"}</span></div>
    </div>`;
}

function breakerCard(name, label, b, spend) {
  const halted = b && b.halted;
  const status = halted ? { cls: "bad", text: "detenido" } : { cls: "ok", text: "operando" };
  return `
    <div class="card">
      <h2>Circuit breaker -- ${label}</h2>
      <span class="pill ${status.cls}"><span class="dot"></span>${status.text}</span>
      ${halted ? `<div class="row"><span class="label">Motivo</span><span>${b.halt_reason ?? "--"}</span></div>` : ""}
      <div class="row"><span class="label">Perdidas seguidas</span><span>${b ? b.consecutive_losses : "--"}</span></div>
      <div class="row"><span class="label">Valor inicio del dia</span><span>${b ? "$" + b.day_start_value.toFixed(2) : "--"}</span></div>
    </div>`;
}

function decisionsTable(rows) {
  if (!rows.length) return `<div class="empty">Todavia no hay decisiones registradas.</div>`;
  const trs = rows.map(r => {
    const signal = r.signal || {};
    const symbol = signal.symbol || signal.inst_id || "--";
    const reasoning = r.review ? r.review.reasoning : (r.reason || "");
    return `<tr>
      <td class="mono">${r.timestamp.replace("T", " ").slice(0, 19)}</td>
      <td>${r.pool}</td>
      <td class="mono">${symbol}</td>
      <td class="result-${r.result}">${r.result}</td>
      <td class="mono">${reasoning}</td>
    </tr>`;
  }).join("");
  return `<table><thead><tr><th>Hora (UTC)</th><th>Pool</th><th>Simbolo</th><th>Resultado</th><th>Detalle</th></tr></thead><tbody>${trs}</tbody></table>`;
}

function tradesTable(rows) {
  if (!rows.length) return `<div class="empty">Todavia no se ejecuto ninguna orden.</div>`;
  const trs = rows.map(r => `<tr>
      <td class="mono">${r.timestamp.replace("T", " ").slice(0, 19)}</td>
      <td>${r.pool}</td>
      <td class="mono">${r.symbol || r.instId || "--"}</td>
      <td>${r.action || r.side || "--"}</td>
      <td>${r.status || "--"}</td>
    </tr>`).join("");
  return `<table><thead><tr><th>Hora (UTC)</th><th>Pool</th><th>Simbolo</th><th>Accion</th><th>Estado</th></tr></thead><tbody>${trs}</tbody></table>`;
}

async function refresh() {
  const res = await fetch("/data");
  const d = await res.json();

  document.getElementById("clock").textContent = "actualizado " + new Date(d.generated_at).toLocaleTimeString();

  document.getElementById("pool-cards").innerHTML =
    poolCard("stocks", "Acciones (IBKR)", d.pools.stocks) +
    poolCard("crypto", "Crypto (OKX)", d.pools.crypto);

  document.getElementById("breaker-cards").innerHTML =
    breakerCard("stocks", "Acciones", d.breakers.stocks, d.spend.stocks) +
    breakerCard("crypto", "Crypto", d.breakers.crypto, d.spend.crypto);

  document.getElementById("decisions-wrap").innerHTML = decisionsTable(d.recent_decisions);
  document.getElementById("trades-wrap").innerHTML = tradesTable(d.recent_trades);

  const errSection = document.getElementById("errors-section");
  if (d.error_count > 0) {
    errSection.hidden = false;
    document.getElementById("errors-summary").textContent =
      `${d.error_count} error(es) registrado(s) -- ver el mas reciente (${d.last_error.pool}, ${d.last_error.timestamp})`;
    document.getElementById("error-trace").textContent = d.last_error.traceback || "(sin detalle)";
  } else {
    errSection.hidden = true;
  }
}

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
