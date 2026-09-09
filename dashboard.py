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


def _last_scan() -> dict | None:
    """The most recent tournament bookkeeping line, so the page can show
    how wide the last scan actually looked."""
    for entry in reversed(_tail_jsonl(LOGS_DIR / "decisions.log", 500)):
        if entry.get("result") == "tournament":
            return entry
    return None


def build_data() -> dict:
    decisions = _tail_jsonl(LOGS_DIR / "decisions.log", 500)
    trades = _tail_jsonl(LOGS_DIR / "trades.log", 20)
    errors = [e for e in decisions if e.get("result") == "error" and "timestamp" in e and _age_minutes(e["timestamp"]) < 24 * 60]
    # Old, already-resolved log lines (e.g. pre-fix connection errors from
    # days ago) shouldn't pad out "recent" once today's activity is thin --
    # that reads as something currently wrong when it's just history.
    recent = [d for d in decisions if "timestamp" in d and _age_minutes(d["timestamp"]) < 48 * 60]
    # Bookkeeping lines are for the scan widget, not the decision feed.
    feed = [d for d in recent if d.get("result") != "tournament"]

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "pools": {pool: _pool_health(decisions, pool) for pool in ("stocks", "crypto")},
        "positions": {p: v for p in ("stocks", "crypto") if (v := _read_json(STATE_DIR / f"positions_{p}.json"))},
        "breakers": {p: v for p in ("stocks", "crypto") if (v := _read_json(STATE_DIR / f"breaker_{p}.json"))},
        "spend": {p: v for p in ("stocks", "crypto") if (v := _read_json(STATE_DIR / f"spend_{p}.json"))},
        "standings": _standings(),
        "last_scan": _last_scan(),
        "panel_sentiment": _panel_sentiment(decisions),
        "error_count": len(errors),
        "last_error": errors[-1] if errors else None,
        "recent_decisions": list(reversed(feed))[:30],
        "recent_trades": list(reversed(trades)),
    }


_HTML = """<!doctype html>
<html lang="es">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Trading Agent</title>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Fira+Code:wght@400;500;600&family=Fira+Sans:wght@400;500;600;700&display=swap">
<style>
  :root {
    --bg: #020617; --card: #0e1223; --card-2: #141a30; --border: #334155;
    --fg: #f8fafc; --muted: #94a3b8;
    /* --accent is the P&L color: green = gaining, and reused for "healthy"
       status. --glow is a separate identity color (console/system-alive),
       never used for a financial value, so the two meanings never collide. */
    --accent: #22c55e; --accent-soft: rgba(34,197,94,0.15); --on-accent: #06210f;
    --glow: #22d3ee; --glow-soft: rgba(34,211,238,0.14);
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
      --warn: #b45309; --warn-soft: rgba(180,83,9,0.12);
      --bad: #dc2626; --bad-soft: rgba(220,38,38,0.12);
    }
  }
  :root[data-theme="light"] {
    --bg: #f8fafc; --card: #ffffff; --card-2: #eef2f7; --border: #dbe2ea;
    --fg: #0f172a; --muted: #64748b;
    --accent: #16a34a; --accent-soft: rgba(22,163,74,0.12); --on-accent: #ffffff;
    --glow: #0891b2; --glow-soft: rgba(8,145,178,0.10);
    --warn: #b45309; --warn-soft: rgba(180,83,9,0.12);
    --bad: #dc2626; --bad-soft: rgba(220,38,38,0.12);
  }

  * { box-sizing: border-box; }
  html, body { background: var(--bg); }
  body {
    margin: 0; color: var(--fg); background: var(--bg); font-family: var(--font-body);
    padding: 24px clamp(14px, 3.5vw, 40px) 64px; font-size: 14px; line-height: 1.5;
  }
  ::selection { background: var(--accent-soft); }
  a { color: var(--accent); }
  button { font: inherit; cursor: pointer; }
  :focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
  svg.icon { width: 14px; height: 14px; flex: none; }

  .masthead {
    display: flex; align-items: center; justify-content: space-between; flex-wrap: wrap; gap: 14px;
    border-bottom: 1px solid var(--border); padding-bottom: 14px; margin-bottom: 22px;
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
  .updated { color: var(--muted); font-family: var(--font-mono); font-size: 12px; white-space: nowrap; display: flex; align-items: center; gap: 6px; }

  section { margin-bottom: 24px; }
  .section-label { font-size: 11px; text-transform: uppercase; letter-spacing: 0.08em; color: var(--muted); font-weight: 600; margin: 0 0 10px; }

  .pools { display: grid; grid-template-columns: repeat(auto-fit, minmax(270px, 1fr)); gap: 12px; }
  .pool-card { background: var(--card); border: 1px solid var(--border); border-radius: 10px; padding: 16px 18px; position: relative; overflow: hidden; }
  .pool-card::before { content: ""; position: absolute; inset: 0 auto 0 0; width: 3px; background: var(--border); }
  .pool-card[data-state="ok"]::before { background: var(--accent); }
  .pool-card[data-state="warn"]::before { background: var(--warn); }
  .pool-card[data-state="bad"]::before { background: var(--bad); }
  .pool-head { display: flex; align-items: center; justify-content: space-between; margin-bottom: 12px; gap: 10px; }
  .pool-head h2 { font-size: 14px; font-weight: 600; margin: 0; }

  .chip { display: inline-flex; align-items: center; gap: 5px; padding: 3px 9px; border-radius: 6px; font-size: 11px; font-weight: 600; text-transform: uppercase; letter-spacing: 0.03em; white-space: nowrap; }
  .chip.ok { background: var(--accent-soft); color: var(--accent); }
  .chip.warn { background: var(--warn-soft); color: var(--warn); }
  .chip.bad { background: var(--bad-soft); color: var(--bad); }

  .pool-meta { display: flex; justify-content: space-between; align-items: baseline; margin-bottom: 12px; gap: 10px; }
  .pool-meta .age { font-family: var(--font-mono); font-size: 13px; }
  .pool-meta .result { font-family: var(--font-mono); font-size: 12px; color: var(--muted); }

  .ticks { display: flex; gap: 3px; }
  .ticks .tick { width: 100%; height: 5px; border-radius: 2px; background: var(--card-2); }
  .ticks .tick.ok { background: var(--accent); }
  .ticks .tick.bad { background: var(--bad); }
  .ticks .tick.warn { background: var(--warn); }

  .breakers { display: grid; grid-template-columns: repeat(auto-fit, minmax(230px, 1fr)); gap: 12px; }
  .breaker-card { background: var(--card); border: 1px solid var(--border); border-radius: 10px; padding: 14px 18px; display: flex; flex-direction: column; gap: 8px; }
  .breaker-head { display: flex; align-items: center; justify-content: space-between; gap: 10px; }
  .breaker-head h3 { font-size: 11.5px; font-weight: 600; margin: 0; color: var(--muted); text-transform: uppercase; letter-spacing: 0.04em; }
  .kv { display: flex; justify-content: space-between; font-size: 12.5px; gap: 10px; }
  .kv .k { color: var(--muted); }
  .kv .v { font-family: var(--font-mono); font-variant-numeric: tabular-nums; }

  .momentum-row { display: grid; grid-template-columns: 2.2fr 1fr; gap: 12px; align-items: stretch; }
  @media (max-width: 700px) { .momentum-row { grid-template-columns: 1fr; } }

  .chart-card { background: var(--card); border: 1px solid var(--border); border-radius: 10px; padding: 16px 18px; }
  .chart-head { display: flex; justify-content: space-between; align-items: baseline; margin-bottom: 10px; gap: 10px; flex-wrap: wrap; }
  .chart-head h2 { font-size: 13px; font-weight: 600; margin: 0; }
  .chart-head .now { font-family: var(--font-mono); font-size: 12.5px; color: var(--muted); }
  .chart-head .now b { color: var(--fg); font-weight: 600; }

  .gauge-card { display: flex; flex-direction: column; align-items: center; justify-content: center; text-align: center; gap: 2px; }
  .gauge-card h2 { font-size: 13px; font-weight: 600; margin: 0 0 8px; align-self: flex-start; }
  .gauge-ring { position: relative; width: 108px; height: 108px; }
  .gauge-ring svg { width: 100%; height: 100%; transform: rotate(-90deg); }
  .gauge-ring .track { fill: none; stroke: var(--card-2); stroke-width: 9; }
  .gauge-ring .fill { fill: none; stroke: var(--glow); stroke-width: 9; stroke-linecap: round; filter: drop-shadow(0 0 4px var(--glow-soft)); transition: stroke-dasharray 0.4s ease; }
  .gauge-ring .figure {
    position: absolute; inset: 0; display: flex; align-items: center; justify-content: center;
    font-family: var(--font-mono); font-size: 20px; font-weight: 600; font-variant-numeric: tabular-nums;
  }
  .gauge-card .sub { font-size: 11.5px; color: var(--muted); margin-top: 8px; }
  svg.rsi-chart { width: 100%; height: auto; display: block; }
  .rsi-chart .grid-line { stroke: var(--border); stroke-width: 1; }
  .rsi-chart .axis-label { fill: var(--muted); font-family: var(--font-mono); font-size: 9px; }
  .rsi-chart .area { fill: var(--accent-soft); }
  .rsi-chart .line { fill: none; stroke: var(--accent); stroke-width: 1.75; stroke-linejoin: round; stroke-linecap: round; }
  .rsi-chart .overbought { fill: var(--bad-soft); }
  .rsi-chart .zone-line { stroke: var(--bad); stroke-width: 1; stroke-dasharray: 3 3; opacity: 0.7; }
  .rsi-chart .dot-last { fill: var(--accent); }

  .table-scroll { overflow-x: auto; background: var(--card); border: 1px solid var(--border); border-radius: 10px; }
  table { width: 100%; border-collapse: collapse; min-width: 620px; }
  th, td { text-align: left; padding: 10px 14px; font-size: 12.5px; border-bottom: 1px solid var(--border); white-space: nowrap; }
  td.wrap { white-space: normal; }
  th { color: var(--muted); font-weight: 600; font-size: 10px; text-transform: uppercase; letter-spacing: 0.05em; background: var(--card-2); }
  tbody tr:last-child td { border-bottom: none; }
  tbody tr:hover { background: var(--card-2); }
  td.mono { font-family: var(--font-mono); font-variant-numeric: tabular-nums; }
  .result-chip { display: inline-flex; align-items: center; gap: 4px; padding: 2px 8px; border-radius: 6px; font-size: 10.5px; font-weight: 600; }
  .result-chip.ok { background: var(--accent-soft); color: var(--accent); }
  .result-chip.bad { background: var(--bad-soft); color: var(--bad); }
  .result-chip.muted { background: var(--card-2); color: var(--muted); }
  .detail { color: var(--muted); font-size: 12px; }
  .indicators { font-family: var(--font-mono); font-size: 11.5px; color: var(--muted); }
  .indicators b { color: var(--fg); font-weight: 500; }
  .indicators .flag { color: var(--warn); }

  .empty { color: var(--muted); font-size: 13px; padding: 26px; text-align: center; font-style: italic; }

  .skel { background: linear-gradient(90deg, var(--card-2) 25%, var(--border) 37%, var(--card-2) 63%); background-size: 400% 100%; border-radius: 6px; animation: skel 1.4s ease infinite; }
  @keyframes skel { 0% { background-position: 100% 0; } 100% { background-position: 0 0; } }
  @media (prefers-reduced-motion: reduce) { .skel { animation: none; } }
  .skel-card { height: 96px; border-radius: 10px; }
  .skel-row { height: 38px; border-radius: 0; }

  .errors-card { background: var(--bad-soft); border: 1px solid var(--bad); border-radius: 10px; padding: 2px 18px; }
  .errors-card summary { cursor: pointer; padding: 13px 0; font-size: 12.5px; font-weight: 600; color: var(--bad); list-style: none; display: flex; align-items: center; gap: 6px; }
  .errors-card summary::-webkit-details-marker { display: none; }
  .errors-card pre { white-space: pre-wrap; word-break: break-word; font-family: var(--font-mono); font-size: 11px; color: var(--fg); margin: 0 0 14px; max-height: 300px; overflow-y: auto; opacity: 0.85; }

  /* Status strip: health is a one-line fact, not two cards' worth of space. */
  .status-strip { display: flex; gap: 6px 20px; align-items: center; flex-wrap: wrap; }
  .status-item { display: flex; align-items: center; gap: 6px; font-size: 12.5px; white-space: nowrap; }
  .status-item .who { color: var(--muted); }
  .status-item .when { font-family: var(--font-mono); color: var(--muted); font-size: 11.5px; }
  .status-item .led { width: 7px; height: 7px; border-radius: 50%; flex: none; }
  .status-item .led.ok { background: var(--accent); }
  .status-item .led.warn { background: var(--warn); }
  .status-item .led.bad { background: var(--bad); }

  .money { display: grid; grid-template-columns: repeat(auto-fit, minmax(190px, 1fr)); gap: 12px; }
  .money-card { background: var(--card); border: 1px solid var(--border); border-radius: 10px; padding: 14px 16px; }
  .money-card .label { font-size: 10px; text-transform: uppercase; letter-spacing: 0.06em; color: var(--muted); font-weight: 600; }
  .money-card .figure { font-family: var(--font-mono); font-size: 22px; font-weight: 500; margin-top: 5px; font-variant-numeric: tabular-nums; letter-spacing: -0.02em; }
  .money-card .figure.muted { color: var(--muted); }
  .money-card .sub { font-size: 11.5px; color: var(--muted); margin-top: 3px; }
  .money-card .sub.bad { color: var(--bad); }

  /* Leaderboard: the point of the page. Grid rather than a real table --
     the return bar needs a flexible track beside fixed numeric columns. */
  .board { background: var(--card); border: 1px solid var(--border); border-radius: 10px; overflow: hidden; }
  .board-scroll { overflow-x: auto; }
  .board-row {
    display: grid; grid-template-columns: 1.4fr 76px 76px 88px minmax(120px, 1.6fr);
    gap: 14px; align-items: center; padding: 12px 16px; border-bottom: 1px solid var(--border); min-width: 560px;
  }
  .board-row:last-of-type { border-bottom: none; }
  .board-head { background: var(--card-2); font-size: 10px; text-transform: uppercase; letter-spacing: 0.05em; color: var(--muted); font-weight: 600; box-shadow: inset 0 2px 0 var(--glow-soft); }
  .board-row .name { font-weight: 600; font-size: 13px; }
  .board-row .name small { display: block; color: var(--muted); font-weight: 400; font-size: 11px; margin-top: 1px; }
  .board-row .num { font-family: var(--font-mono); font-variant-numeric: tabular-nums; font-size: 12.5px; text-align: right; }
  .board-row .num.pos { color: var(--accent); }
  .board-row .num.neg { color: var(--bad); }
  .board-row .num.pending { color: var(--muted); }
  .bar-track { height: 8px; background: var(--card-2); border-radius: 4px; position: relative; overflow: hidden; }
  .bar-fill { position: absolute; top: 0; bottom: 0; border-radius: 4px; }
  .bar-fill.pos { background: var(--accent); }
  .bar-fill.neg { background: var(--bad); }
  .bar-zero { position: absolute; top: -2px; bottom: -2px; width: 1px; background: var(--border); }
  .board-note { padding: 12px 16px; font-size: 12px; color: var(--muted); background: var(--card-2); border-top: 1px solid var(--border); }

  @media (max-width: 480px) {
    table { min-width: 520px; }
    .money-card .figure { font-size: 19px; }
  }
</style>
</head>
<body>
<header class="masthead">
  <div class="brand">
    <span class="pulse-ring" aria-hidden="true"></span>
    <h1>Trading Agent</h1>
  </div>
  <div class="status-strip" id="status-strip" role="status" aria-live="polite"></div>
  <span class="updated" id="clock">&mdash;</span>
</header>

<section aria-label="Estado de las cuentas">
  <p class="section-label">Cuentas &middot; modo papel</p>
  <div class="money" id="money-cards">
    <div class="money-card skel skel-card" aria-hidden="true"></div>
    <div class="money-card skel skel-card" aria-hidden="true"></div>
    <div class="money-card skel skel-card" aria-hidden="true"></div>
  </div>
</section>

<section aria-label="Torneo de estrategias">
  <p class="section-label">Qu&eacute; estrategia va ganando</p>
  <div id="board-wrap">
    <div class="skel skel-card" aria-hidden="true"></div>
  </div>
</section>

<section aria-label="Momentum y sentimiento del panel">
  <p class="section-label">Momentum &middot; RSI(14) reciente, crypto</p>
  <div class="momentum-row">
    <div class="chart-card" id="rsi-chart-wrap"></div>
    <div class="chart-card gauge-card" id="sentiment-gauge-wrap"></div>
  </div>
</section>

<section aria-label="Decisiones recientes">
  <p class="section-label">Decisiones recientes</p>
  <div class="table-scroll" id="decisions-wrap">
    <div class="skel skel-row" aria-hidden="true"></div>
  </div>
</section>

<section aria-label="Ordenes ejecutadas">
  <p class="section-label">Ordenes ejecutadas</p>
  <div class="table-scroll" id="trades-wrap">
    <div class="skel skel-row" aria-hidden="true"></div>
  </div>
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

const ICONS = {
  check: '<svg class="icon" viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="10" cy="10" r="7.25"/><path d="M6.8 10.2l2 2 4.4-4.6"/></svg>',
  warning: '<svg class="icon" viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M10 3.2l7.8 13.5a1 1 0 01-.87 1.5H3.07a1 1 0 01-.87-1.5L10 3.2z"/><path d="M10 8.3v3.6"/><circle cx="10" cy="14.4" r="0.15" fill="currentColor" stroke="none"/></svg>',
  xcircle: '<svg class="icon" viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="10" cy="10" r="7.25"/><path d="M7.3 7.3l5.4 5.4M12.7 7.3l-5.4 5.4"/></svg>',
  clock: '<svg class="icon" viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="10" cy="10" r="7.25"/><path d="M10 5.8v4.4l3 1.8"/></svg>',
};

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

function stateIcon(state) {
  return state === "ok" ? ICONS.check : state === "warn" ? ICONS.warning : ICONS.xcircle;
}

function statusStrip(d) {
  const items = ["stocks", "crypto"].map(pool => {
    const h = d.pools[pool];
    const state = h.last_timestamp === null ? "bad" : h.stale ? "warn" : "ok";
    return `<span class="status-item">
      <span class="led ${state}" aria-hidden="true"></span>
      <span class="who">${POOL_LABELS[pool]}</span>
      <span class="when">${fmtAge(h.age_minutes)} \\u00b7 ${h.last_result ?? "\\u2014"}</span>
    </span>`;
  });

  if (d.last_scan) {
    items.push(`<span class="status-item">
      <span class="who">\\u00faltimo escaneo</span>
      <span class="when">${d.last_scan.scanned} pares \\u00b7 ${d.last_scan.proposed} propuestas</span>
    </span>`);
  }
  return items.join("");
}

function moneyCards(d) {
  const cards = [];

  for (const pool of ["crypto", "stocks"]) {
    const b = d.breakers[pool];
    const positions = d.positions[pool] || {};
    const held = Object.keys(positions).length;
    const halted = b && b.halted;
    cards.push(`
      <article class="money-card">
        <div class="label">${POOL_LABELS[pool]}</div>
        <div class="figure${b ? "" : " muted"}">${b ? "$" + b.day_start_value.toFixed(2) : "\\u2014"}</div>
        <div class="sub${halted ? " bad" : ""}">
          ${halted ? "DETENIDO: " + (b.halt_reason ?? "") : `valor al inicio del d\\u00eda \\u00b7 ${held} posici\\u00f3n${held === 1 ? "" : "es"} abierta${held === 1 ? "" : "s"}`}
        </div>
      </article>`);
  }

  const totalLosses = ["crypto", "stocks"]
    .map(p => (d.breakers[p] ? d.breakers[p].consecutive_losses : 0))
    .reduce((a, b) => a + b, 0);
  const anyHalted = ["crypto", "stocks"].some(p => d.breakers[p] && d.breakers[p].halted);
  cards.push(`
    <article class="money-card">
      <div class="label">Protecciones</div>
      <div class="figure${anyHalted ? "" : " muted"}">${anyHalted ? "detenido" : "activas"}</div>
      <div class="sub">${totalLosses} p\\u00e9rdida${totalLosses === 1 ? "" : "s"} seguida${totalLosses === 1 ? "" : "s"} \\u00b7 l\\u00edmite de gasto y corta-circuitos operando</div>
    </article>`);

  return cards.join("");
}

const STRATEGY_BLURB = {
  momentum: "compra lo que ya viene subiendo",
  mean_reversion: "compra lo castigado, apostando al rebote",
  trend_follow: "compra tendencias ya establecidas",
  breakout: "compra la ruptura de un techo reciente",
};

function leaderboard(rows) {
  if (!rows.length) {
    return `<div class="board"><div class="empty">El torneo todav\\u00eda no registr\\u00f3 propuestas. Corr\\u00e9 el orchestrator para empezar a juntar datos.</div></div>`;
  }

  // Bars are scaled against the largest absolute return on the board, so
  // the comparison between strategies stays honest at any magnitude.
  const maxAbs = Math.max(0.001, ...rows.map(r => Math.abs(r.avg_return_pct ?? 0)));
  const anyScored = rows.some(r => r.scored > 0);

  const body = rows.map(r => {
    const v = r.avg_return_pct;
    const pending = v === null || v === undefined;
    const width = pending ? 0 : Math.abs(v) / maxAbs * 50;
    const bar = pending
      ? `<span class="num pending">esperando</span>`
      : `<div class="bar-track" role="img" aria-label="retorno promedio ${v.toFixed(2)}%">
           <div class="bar-zero" style="left:50%"></div>
           <div class="bar-fill ${v >= 0 ? "pos" : "neg"}" style="${v >= 0 ? `left:50%;width:${width}%` : `right:50%;width:${width}%`}"></div>
         </div>`;
    return `
      <div class="board-row">
        <div class="name">${r.strategy}<small>${STRATEGY_BLURB[r.strategy] ?? ""}</small></div>
        <div class="num">${r.open_proposals}</div>
        <div class="num">${r.scored}</div>
        <div class="num ${pending ? "pending" : v >= 0 ? "pos" : "neg"}">${pending ? "\\u2014" : (v >= 0 ? "+" : "") + v.toFixed(2) + "%"}</div>
        ${bar}
      </div>`;
  }).join("");

  const note = anyScored
    ? "Cada propuesta se cierra contra el precio real 24h despu\\u00e9s. 'Cerradas' es lo \\u00fanico que da confianza: con menos de ~30 resultados, el promedio todav\\u00eda es ruido."
    : "Ninguna propuesta cumpli\\u00f3 todav\\u00eda sus 24h. Los primeros resultados aparecen ma\\u00f1ana; la tabla se vuelve informativa reci\\u00e9n con varias decenas.";

  return `
    <div class="board">
      <div class="board-scroll">
        <div class="board-row board-head">
          <div>Estrategia</div><div class="num">Abiertas</div><div class="num">Cerradas</div><div class="num">Promedio</div><div>Rendimiento</div>
        </div>
        ${body}
      </div>
      <p class="board-note">${note}</p>
    </div>`;
}

function resultChip(result) {
  const cls = OK_RESULTS.has(result) ? "ok" : BAD_RESULTS.has(result) ? "bad" : "muted";
  return `<span class="result-chip ${cls}">${result}</span>`;
}

function tradeChip(status) {
  const s = (status || "").toLowerCase();
  const cls = ["fail", "reject", "cancel", "error"].some(w => s.includes(w)) ? "bad" : "ok";
  return `<span class="result-chip ${cls}">${status || "\\u2014"}</span>`;
}

function indicatorsCell(signal) {
  if (!signal || !signal.indicators) return "";
  const ind = signal.indicators;
  const rsi = ind.rsi_14;
  const overbought = rsi !== null && rsi !== undefined && rsi >= 80;
  const rsiTxt = rsi === null || rsi === undefined ? "\\u2014" : rsi.toFixed(1);
  const kronos = signal.kronos_forecast;
  const kronosTxt = kronos ? ` &middot; K ${kronos.predicted_change_pct >= 0 ? "+" : ""}${kronos.predicted_change_pct}%` : "";
  return `<span class="indicators">RSI <b class="${overbought ? 'flag' : ''}">${rsiTxt}</b> &middot; ${ind.sma_trend || "\\u2014"}${kronosTxt}</span>`;
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
      <td>${indicatorsCell(signal)}</td>
      <td>${resultChip(r.result)}</td>
      <td class="detail wrap">${reasoning}</td>
    </tr>`;
  }).join("");
  return `<table><thead><tr><th>Hora (UTC)</th><th>Pool</th><th>S\\u00edmbolo</th><th>Indicadores</th><th>Resultado</th><th>Detalle</th></tr></thead><tbody>${trs}</tbody></table>`;
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

function rsiChart(allDecisions) {
  const pts = allDecisions
    .filter(d => d.pool === "crypto" && d.signal && d.signal.indicators && typeof d.signal.indicators.rsi_14 === "number")
    .slice(0, 24).reverse()
    .map(d => d.signal.indicators.rsi_14);

  if (pts.length < 2) {
    return `<div class="empty">Todav\\u00eda no hay suficientes lecturas de RSI para graficar.</div>`;
  }

  // Scale to the data's own range (with headroom), not a fixed 0-100:
  // real RSI readings cluster in a narrow band, and a forced 0-100 axis
  // flattens them into a straight line against the top edge.
  const W = 1200, H = 130;
  const PAD_L = 34, PAD_R = 10, PAD_T = 12, PAD_B = 20;
  const lo = Math.max(0, Math.min(...pts, 80) - 6);
  const hi = Math.min(100, Math.max(...pts, 80) + 6);
  const x = i => PAD_L + (i / (pts.length - 1)) * (W - PAD_L - PAD_R);
  const y = v => PAD_T + (1 - (Math.min(hi, Math.max(lo, v)) - lo) / (hi - lo)) * (H - PAD_T - PAD_B);

  const linePath = pts.map((v, i) => `${i === 0 ? "M" : "L"} ${x(i).toFixed(1)} ${y(v).toFixed(1)}`).join(" ");
  const areaPath = `${linePath} L ${x(pts.length - 1).toFixed(1)} ${(H - PAD_B).toFixed(1)} L ${x(0).toFixed(1)} ${(H - PAD_B).toFixed(1)} Z`;
  const last = pts[pts.length - 1];
  const overboughtY = y(80);
  const showOverbought = 80 >= lo && 80 <= hi;

  // Y ticks at the range ends plus the 80 line when it's in view, so
  // every label names a value the chart actually reaches.
  const ticks = [lo, hi].map(v => `
      <line x1="${PAD_L}" y1="${y(v).toFixed(1)}" x2="${W - PAD_R}" y2="${y(v).toFixed(1)}" class="grid-line"></line>
      <text x="${PAD_L - 5}" y="${(y(v) + 3).toFixed(1)}" text-anchor="end" class="axis-label">${v.toFixed(0)}</text>`).join("");

  return `
    <div class="chart-head">
      <h2>RSI(14) &mdash; \\u00faltimas ${pts.length} corridas de crypto</h2>
      <span class="now">actual: <b>${last.toFixed(1)}</b>${last >= 80 ? " \\u00b7 sobrecompra" : last <= 30 ? " \\u00b7 sobreventa" : " \\u00b7 neutral"}</span>
    </div>
    <svg class="rsi-chart" viewBox="0 0 ${W} ${H}" role="img" aria-label="RSI de las \\u00faltimas ${pts.length} corridas de crypto, entre ${Math.min(...pts).toFixed(1)} y ${Math.max(...pts).toFixed(1)}, actual ${last.toFixed(1)}. El detalle por corrida est\\u00e1 en la tabla de decisiones.">
      ${ticks}
      ${showOverbought ? `
      <rect x="${PAD_L}" y="${PAD_T}" width="${W - PAD_L - PAD_R}" height="${Math.max(0, overboughtY - PAD_T).toFixed(1)}" class="overbought"></rect>
      <line x1="${PAD_L}" y1="${overboughtY.toFixed(1)}" x2="${W - PAD_R}" y2="${overboughtY.toFixed(1)}" class="zone-line"></line>
      <text x="${PAD_L - 5}" y="${(overboughtY + 3).toFixed(1)}" text-anchor="end" class="axis-label">80</text>` : ""}
      <path d="${areaPath}" class="area"></path>
      <path d="${linePath}" class="line"></path>
      <circle cx="${x(pts.length - 1).toFixed(1)}" cy="${y(last).toFixed(1)}" r="3.5" class="dot-last"></circle>
      <text x="${PAD_L}" y="${H - 5}" class="axis-label">m\\u00e1s antiguo</text>
      <text x="${W - PAD_R}" y="${H - 5}" text-anchor="end" class="axis-label">ahora</text>
    </svg>`;
}

function sentimentGauge(s) {
  if (!s) {
    return `<h2>Sentimiento del panel</h2><div class="empty" style="padding:10px 0">Sin datos todav\\u00eda</div>`;
  }
  const r = 40, c = 2 * Math.PI * r;
  const frac = s.buy_pct / 100;
  const mood = s.buy_pct >= 60 ? "alcista" : s.buy_pct <= 15 ? "muy cauto" : "cauto";
  return `
    <h2>Sentimiento del panel</h2>
    <div class="gauge-ring" role="img" aria-label="${s.buy_pct}% de los votos del panel fueron compra, sobre las \\u00faltimas ${s.sample} decisiones de crypto">
      <svg viewBox="0 0 100 100">
        <circle cx="50" cy="50" r="${r}" class="track"></circle>
        <circle cx="50" cy="50" r="${r}" class="fill" stroke-dasharray="${(frac * c).toFixed(1)} ${c.toFixed(1)}"></circle>
      </svg>
      <span class="figure">${s.buy_pct}%</span>
    </div>
    <p class="sub">votos de compra \\u00b7 \\u00faltimas ${s.sample} decisiones \\u00b7 ${mood}</p>`;
}

async function refresh() {
  const res = await fetch("/data");
  const d = await res.json();

  document.getElementById("clock").textContent = "actualizado " + new Date(d.generated_at).toLocaleTimeString();
  document.getElementById("status-strip").innerHTML = statusStrip(d);
  document.getElementById("money-cards").innerHTML = moneyCards(d);
  document.getElementById("board-wrap").innerHTML = leaderboard(d.standings);
  document.getElementById("rsi-chart-wrap").innerHTML = rsiChart(d.recent_decisions);
  document.getElementById("sentiment-gauge-wrap").innerHTML = sentimentGauge(d.panel_sentiment);
  document.getElementById("decisions-wrap").innerHTML = decisionsTable(d.recent_decisions);
  document.getElementById("trades-wrap").innerHTML = tradesTable(d.recent_trades);

  const errSection = document.getElementById("errors-section");
  if (d.error_count > 0) {
    errSection.hidden = false;
    document.getElementById("errors-summary").innerHTML =
      `${ICONS.xcircle} ${d.error_count} error(es) en las \\u00faltimas 24h \\u2014 ver el m\\u00e1s reciente (${d.last_error.pool}, ${d.last_error.timestamp})`;
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
