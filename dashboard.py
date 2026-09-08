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
<title>Trading Agent</title>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Fraunces:opsz,wght@9..144,500;9..144,600&family=IBM+Plex+Sans:wght@400;500;600&family=IBM+Plex+Mono:wght@400;500&display=swap">
<style>
  :root {
    --ink: #0d1420; --panel: #131c2c; --panel-2: #182338; --line: #26324a;
    --text: #eef1f7; --muted: #8996b3;
    --accent: #c9992f; --accent-soft: rgba(201,153,47,0.16);
    --ok: #3fb87f; --ok-soft: rgba(63,184,127,0.16);
    --bad: #e2596b; --bad-soft: rgba(226,89,107,0.16);
    --warn: #d4a039; --warn-soft: rgba(212,160,57,0.16);
    --font-display: 'Fraunces', Georgia, 'Times New Roman', serif;
    --font-body: 'IBM Plex Sans', -apple-system, 'Segoe UI', sans-serif;
    --font-mono: 'IBM Plex Mono', ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  }
  @media (prefers-color-scheme: light) {
    :root:not([data-theme="dark"]) {
      --ink: #f6f4ee; --panel: #ffffff; --panel-2: #efece1; --line: #ddd6c4;
      --text: #1c2130; --muted: #6b7280;
      --accent: #9c7420; --accent-soft: rgba(156,116,32,0.12);
      --ok: #1f8f5f; --ok-soft: rgba(31,143,95,0.12);
      --bad: #c53d52; --bad-soft: rgba(197,61,82,0.12);
      --warn: #9c7420; --warn-soft: rgba(156,116,32,0.12);
    }
  }
  :root[data-theme="light"] {
    --ink: #f6f4ee; --panel: #ffffff; --panel-2: #efece1; --line: #ddd6c4;
    --text: #1c2130; --muted: #6b7280;
    --accent: #9c7420; --accent-soft: rgba(156,116,32,0.12);
    --ok: #1f8f5f; --ok-soft: rgba(31,143,95,0.12);
    --bad: #c53d52; --bad-soft: rgba(197,61,82,0.12);
    --warn: #9c7420; --warn-soft: rgba(156,116,32,0.12);
  }

  * { box-sizing: border-box; }
  html, body { background: var(--ink); }
  body {
    margin: 0; color: var(--text); background: var(--ink); font-family: var(--font-body);
    padding: 28px clamp(16px, 4vw, 48px) 64px;
  }
  ::selection { background: var(--accent-soft); }
  a { color: var(--accent); }
  :focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }

  .masthead {
    display: flex; align-items: baseline; justify-content: space-between; flex-wrap: wrap; gap: 16px;
    border-bottom: 1px solid var(--line); padding-bottom: 16px; margin-bottom: 28px;
  }
  .brand { display: flex; align-items: baseline; gap: 10px; }
  .brand .pulse {
    width: 8px; height: 8px; border-radius: 50%; background: var(--ok); flex: none; transform: translateY(-3px);
    animation: pulse 2.4s ease-out infinite;
  }
  @media (prefers-reduced-motion: reduce) { .brand .pulse { animation: none; } }
  @keyframes pulse {
    0% { box-shadow: 0 0 0 0 rgba(63,184,127,0.45); }
    70% { box-shadow: 0 0 0 8px rgba(63,184,127,0); }
    100% { box-shadow: 0 0 0 0 rgba(63,184,127,0); }
  }
  h1 { font-family: var(--font-display); font-weight: 600; font-size: 26px; margin: 0; text-wrap: balance; }
  .updated { color: var(--muted); font-family: var(--font-mono); font-size: 12px; white-space: nowrap; }

  section { margin-bottom: 28px; }
  .section-label { font-size: 11px; text-transform: uppercase; letter-spacing: 0.08em; color: var(--muted); font-weight: 600; margin: 0 0 12px; }

  .pools { display: grid; grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); gap: 16px; }
  .pool-card { background: var(--panel); border: 1px solid var(--line); border-radius: 14px; padding: 20px 22px; position: relative; overflow: hidden; }
  .pool-card::before { content: ""; position: absolute; inset: 0 auto 0 0; width: 3px; background: var(--line); }
  .pool-card[data-state="ok"]::before { background: var(--ok); }
  .pool-card[data-state="warn"]::before { background: var(--warn); }
  .pool-card[data-state="bad"]::before { background: var(--bad); }
  .pool-head { display: flex; align-items: center; justify-content: space-between; margin-bottom: 14px; gap: 10px; }
  .pool-head h2 { font-family: var(--font-display); font-size: 19px; font-weight: 600; margin: 0; }

  .chip { display: inline-flex; align-items: center; gap: 6px; padding: 4px 11px; border-radius: 999px; font-size: 11.5px; font-weight: 600; text-transform: uppercase; letter-spacing: 0.04em; white-space: nowrap; }
  .chip .dot { width: 6px; height: 6px; border-radius: 50%; background: currentColor; }
  .chip.ok { background: var(--ok-soft); color: var(--ok); }
  .chip.warn { background: var(--warn-soft); color: var(--warn); }
  .chip.bad { background: var(--bad-soft); color: var(--bad); }

  .pool-meta { display: flex; justify-content: space-between; align-items: baseline; margin-bottom: 14px; gap: 10px; }
  .pool-meta .age { font-family: var(--font-mono); font-size: 13px; }
  .pool-meta .result { font-family: var(--font-mono); font-size: 12px; color: var(--muted); }

  .ticks { display: flex; gap: 4px; }
  .ticks .tick { width: 100%; height: 6px; border-radius: 3px; background: var(--panel-2); }
  .ticks .tick.ok { background: var(--ok); }
  .ticks .tick.bad { background: var(--bad); }
  .ticks .tick.warn { background: var(--warn); }

  .breakers { display: grid; grid-template-columns: repeat(auto-fit, minmax(240px, 1fr)); gap: 16px; }
  .breaker-card { background: var(--panel); border: 1px solid var(--line); border-radius: 14px; padding: 16px 20px; display: flex; flex-direction: column; gap: 10px; }
  .breaker-head { display: flex; align-items: center; justify-content: space-between; gap: 10px; }
  .breaker-head h3 { font-size: 12.5px; font-weight: 600; margin: 0; color: var(--muted); text-transform: uppercase; letter-spacing: 0.04em; }
  .kv { display: flex; justify-content: space-between; font-size: 13px; gap: 10px; }
  .kv .k { color: var(--muted); }
  .kv .v { font-family: var(--font-mono); font-variant-numeric: tabular-nums; }

  .table-scroll { overflow-x: auto; background: var(--panel); border: 1px solid var(--line); border-radius: 14px; }
  table { width: 100%; border-collapse: collapse; min-width: 580px; }
  th, td { text-align: left; padding: 11px 16px; font-size: 13px; border-bottom: 1px solid var(--line); white-space: nowrap; }
  td.wrap { white-space: normal; }
  th { color: var(--muted); font-weight: 600; font-size: 10.5px; text-transform: uppercase; letter-spacing: 0.06em; background: var(--panel-2); }
  tbody tr:last-child td { border-bottom: none; }
  tbody tr:hover { background: var(--panel-2); }
  td.mono { font-family: var(--font-mono); font-variant-numeric: tabular-nums; }
  .result-chip { display: inline-block; padding: 2px 9px; border-radius: 999px; font-size: 11px; font-weight: 600; }
  .result-chip.ok { background: var(--ok-soft); color: var(--ok); }
  .result-chip.bad { background: var(--bad-soft); color: var(--bad); }
  .result-chip.muted { background: var(--panel-2); color: var(--muted); }
  .detail { color: var(--muted); font-size: 12.5px; }

  .empty { color: var(--muted); font-size: 13px; padding: 28px; text-align: center; font-style: italic; }

  .errors-card { background: var(--bad-soft); border: 1px solid var(--bad); border-radius: 14px; padding: 4px 20px; }
  .errors-card summary { cursor: pointer; padding: 14px 0; font-size: 13px; font-weight: 600; color: var(--bad); list-style: none; }
  .errors-card summary::-webkit-details-marker { display: none; }
  .errors-card summary::before { content: "\\25B8  "; }
  .errors-card details[open] summary::before { content: "\\25BE  "; }
  .errors-card pre { white-space: pre-wrap; word-break: break-word; font-family: var(--font-mono); font-size: 11.5px; color: var(--text); margin: 0 0 16px; max-height: 300px; overflow-y: auto; opacity: 0.85; }
</style>
</head>
<body>
<header class="masthead">
  <div class="brand">
    <span class="pulse" aria-hidden="true"></span>
    <h1>Trading Agent</h1>
  </div>
  <span class="updated" id="clock">&mdash;</span>
</header>

<section aria-label="Estado de los pools">
  <p class="section-label">Estado en vivo</p>
  <div class="pools" id="pool-cards"></div>
</section>

<section aria-label="Circuit breakers">
  <p class="section-label">Circuit breakers</p>
  <div class="breakers" id="breaker-cards"></div>
</section>

<section aria-label="Decisiones recientes">
  <p class="section-label">Decisiones recientes</p>
  <div class="table-scroll" id="decisions-wrap"></div>
</section>

<section aria-label="Ordenes ejecutadas">
  <p class="section-label">Ordenes ejecutadas</p>
  <div class="table-scroll" id="trades-wrap"></div>
</section>

<section id="errors-section" hidden>
  <details class="errors-card">
    <summary id="errors-summary"></summary>
    <pre id="error-trace"></pre>
  </details>
</section>

<script>
const POOL_LABELS = { stocks: "Acciones \\u00b7 IBKR", crypto: "Crypto \\u00b7 OKX" };
const OK_RESULTS = new Set(["executed", "smoketest_opened", "smoketest_closed"]);
const BAD_RESULTS = new Set(["error", "halted", "rejected_by_spend_guard"]);

function fmtAge(mins) {
  if (mins === null || mins === undefined) return "sin datos";
  if (mins < 1) return "hace segundos";
  if (mins < 60) return `hace ${Math.round(mins)} min`;
  return `hace ${(mins / 60).toFixed(1)} h`;
}

function tickClass(result) {
  if (OK_RESULTS.has(result)) return "ok";
  if (BAD_RESULTS.has(result)) return "bad";
  return "";
}

function poolCard(pool, h, allDecisions) {
  const state = h.last_timestamp === null ? "bad" : h.stale ? "warn" : "ok";
  const chipText = h.last_timestamp === null ? "sin corridas" : h.stale ? "atrasado" : "al d\\u00eda";
  const own = allDecisions.filter(d => d.pool === pool || d.pool === "both").slice(0, 10).reverse();
  const pad = Math.max(0, 10 - own.length);
  const ticks = "<span class=\\"tick\\"></span>".repeat(pad) +
    own.map(d => `<span class="tick ${tickClass(d.result)}" title="${d.result}"></span>`).join("");
  return `
    <article class="pool-card" data-state="${state}">
      <div class="pool-head">
        <h2>${POOL_LABELS[pool]}</h2>
        <span class="chip ${state}"><span class="dot"></span>${chipText}</span>
      </div>
      <div class="pool-meta">
        <span class="age">${fmtAge(h.age_minutes)}</span>
        <span class="result">${h.last_result ?? "\\u2014"}</span>
      </div>
      <div class="ticks">${ticks}</div>
    </article>`;
}

function breakerCard(label, b) {
  const halted = b && b.halted;
  const state = halted ? "bad" : "ok";
  return `
    <article class="breaker-card">
      <div class="breaker-head">
        <h3>${label}</h3>
        <span class="chip ${state}"><span class="dot"></span>${halted ? "detenido" : "operando"}</span>
      </div>
      ${halted ? `<div class="kv"><span class="k">Motivo</span><span class="v">${b.halt_reason ?? "\\u2014"}</span></div>` : ""}
      <div class="kv"><span class="k">P\\u00e9rdidas seguidas</span><span class="v">${b ? b.consecutive_losses : "\\u2014"}</span></div>
      <div class="kv"><span class="k">Valor inicio del d\\u00eda</span><span class="v">${b ? "$" + b.day_start_value.toFixed(2) : "\\u2014"}</span></div>
    </article>`;
}

function resultChip(result) {
  const cls = OK_RESULTS.has(result) ? "ok" : BAD_RESULTS.has(result) ? "bad" : "muted";
  return `<span class="result-chip ${cls}">${result}</span>`;
}

function tradeChip(status) {
  const s = (status || "").toLowerCase();
  const cls = (s.includes("fail") || s.includes("reject") || s.includes("cancel")) ? "bad" : "ok";
  return `<span class="result-chip ${cls}">${status || "\\u2014"}</span>`;
}

function decisionsTable(rows) {
  if (!rows.length) return `<div class="empty">Todav\\u00eda no hay decisiones registradas.</div>`;
  const trs = rows.map(r => {
    const signal = r.signal || {};
    const symbol = signal.symbol || signal.inst_id || "\\u2014";
    const reasoning = r.review ? r.review.reasoning : (r.reason || "");
    return `<tr>
      <td class="mono">${r.timestamp.replace("T", " ").slice(0, 19)}</td>
      <td>${r.pool}</td>
      <td class="mono">${symbol}</td>
      <td>${resultChip(r.result)}</td>
      <td class="detail wrap">${reasoning}</td>
    </tr>`;
  }).join("");
  return `<table><thead><tr><th>Hora (UTC)</th><th>Pool</th><th>S\\u00edmbolo</th><th>Resultado</th><th>Detalle</th></tr></thead><tbody>${trs}</tbody></table>`;
}

function tradesTable(rows) {
  if (!rows.length) return `<div class="empty">Todav\\u00eda no se ejecut\\u00f3 ninguna orden.</div>`;
  const trs = rows.map(r => `<tr>
      <td class="mono">${r.timestamp.replace("T", " ").slice(0, 19)}</td>
      <td>${r.pool}</td>
      <td class="mono">${r.symbol || r.instId || "\\u2014"}</td>
      <td>${r.action || r.side || "\\u2014"}</td>
      <td>${tradeChip(r.status)}</td>
    </tr>`).join("");
  return `<table><thead><tr><th>Hora (UTC)</th><th>Pool</th><th>S\\u00edmbolo</th><th>Acci\\u00f3n</th><th>Estado</th></tr></thead><tbody>${trs}</tbody></table>`;
}

async function refresh() {
  const res = await fetch("/data");
  const d = await res.json();

  document.getElementById("clock").textContent = "actualizado " + new Date(d.generated_at).toLocaleTimeString();

  document.getElementById("pool-cards").innerHTML =
    poolCard("stocks", d.pools.stocks, d.recent_decisions) +
    poolCard("crypto", d.pools.crypto, d.recent_decisions);

  document.getElementById("breaker-cards").innerHTML =
    breakerCard("Acciones", d.breakers.stocks) +
    breakerCard("Crypto", d.breakers.crypto);

  document.getElementById("decisions-wrap").innerHTML = decisionsTable(d.recent_decisions);
  document.getElementById("trades-wrap").innerHTML = tradesTable(d.recent_trades);

  const errSection = document.getElementById("errors-section");
  if (d.error_count > 0) {
    errSection.hidden = false;
    document.getElementById("errors-summary").textContent =
      `${d.error_count} error(es) en las \\u00faltimas 24h \\u2014 ver el m\\u00e1s reciente (${d.last_error.pool}, ${d.last_error.timestamp})`;
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
