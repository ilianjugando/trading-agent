# trading-agent

Small autonomous trading pipeline. Stocks (via IBKR) trade on Darvas box
breakouts — buy when price clears a tight recent consolidation range on
above-average volume, with the box bottom doubling as the stop-loss, and
the stop trailed up as new boxes form. Crypto (via OKX) trades on 24h
momentum. Every proposed trade is reviewed by Gemini, then gated by
hard-coded risk limits that no model output can override. See
`.claude/plans` in the parent conversation for the full design rationale.

**This places real orders in live mode. Always validate in paper mode first.**

## 1. Setup

```
py -3 -m venv .venv
.venv\Scripts\pip install -r requirements.txt
copy .env.example .env
```

Edit `.env`:
- `GEMINI_API_KEY` — from https://aistudio.google.com/apikey (free tier)
- `NVIDIA_API_KEY` — optional, from https://build.nvidia.com. Adds a panel of
  extra models alongside Gemini for the trade review vote — any single buy
  vote wins (changed 2026-09-09 from a >=2/3 majority rule after real data
  showed the majority rule was vetoing candidates on ordinary model-to-model
  disagreement rather than genuine risk signal; see `signals/llm_review.py`).
  Leave blank to use Gemini alone.
- `OKX_DEMO_*` — create Demo Trading API keys at OKX. These are **not** a
  toggle on the normal API Management page — go to Trade > Demo Trading,
  find Personal Center *inside* that section, then Demo Trading API >
  Create Demo Trading API Key. Keys made this way are inherently
  demo-scoped, separate from your live account's keys.
- `OKX_*` — live API keys, only needed once you're ready for `--mode live`
- IBKR vars usually don't need changes unless you run IB Gateway on a
  non-default port

## 2. Install IB Gateway (required for the stocks side)

Download IB Gateway (lighter than full TWS) from Interactive Brokers, log
into your **paper trading** account first (a separate username IBKR
provisions for you — Client Portal > Settings > Paper Trading Account
shows it, account numbers start with `DU`), and leave it running. Paper
port is 4002 by default in Gateway — confirm under Configure > Settings >
API > Settings. Live account uses port 4001. (These differ from TWS's
7497/7496 — Gateway and TWS use different port conventions even for the
same account type.) The orchestrator connects to whichever port matches
`--mode`.

## 3. Run tests

```
.venv\Scripts\python -m pytest tests\ -v
```

These test the risk guardrails (`spend_guard`, `circuit_breaker`) in
isolation — no broker or API connection needed.

## 4. Run in paper mode

With IB Gateway logged into the paper account and OKX Demo Trading keys set:

```
.venv\Scripts\python execution\orchestrator.py --mode paper --pool both
```

Check `logs/decisions.log` (every signal considered, including skips,
rejections, stop-loss exits, and trailed stops) and `logs/trades.log`
(only executed orders). Open positions and their current stop level live
in `state/positions_stocks.json` — inspect it any time to see what the
agent thinks it's holding and where its stop sits. Run this for a while —
days, not minutes — before trusting it with the real $150.

Or skip reading raw JSON and run `python dashboard.py`, then open
http://127.0.0.1:8787 — a local read-only page showing pool health (with
a staleness warning if a scheduled run stopped firing), circuit breaker
status, and recent decisions/trades. Polls every 5s; doesn't run on a
schedule, start it manually when you want to look.

## 5. Schedule it (Windows Task Scheduler)

One run does one pass and exits, so scheduling repetition is Task
Scheduler's job, not the script's. Two separate tasks, since crypto
trades 24/7 but `run_stocks()` gates itself on real NYSE hours
(`_market_is_open()` in `orchestrator.py`) regardless of when it's fired:

- **`TradingAgentPaper`** — `--pool stocks`, daily, repeat every 30min,
  window wide enough to cover both US daylight-saving states (the code's
  own market-hours check is the real gate, not this window)
- **`TradingAgentCrypto`** — `--pool crypto`, daily, repeat every 15min,
  ~24h window. Deliberately faster than stocks: the 24h-change ranking
  is computed from a live ticker (moves within the hour), unlike the
  RSI/SMA confirmation indicators which read daily bars and are the
  same value all day regardless of scan frequency -- there's no
  benefit to polling those any faster than once a day, but there is a
  real benefit to catching a fresh mover sooner rather than waiting up
  to an hour. 15min is fast enough to matter without meaningfully
  increasing OKX/LLM API call volume (still well under both providers'
  rate limits).

For either: Task Scheduler > Create Task > Action "Start a program",
Program `C:\Users\ivasc\trading-agent\.venv\Scripts\python.exe`,
Arguments `execution\orchestrator.py --mode paper --pool <stocks|crypto>`.
Make sure IB Gateway is set to auto-restart/stay logged in, or the
stocks leg will fail to connect (it fails safe — logs an error, places
no orders — but won't trade either).

## 6. Going live

Only after paper mode looks correct for a while:
- Fund the accounts (your $100 IBKR transfer, $50 into OKX)
- Change the Task Scheduler action's arguments to `--mode live`
- Watch `logs/` closely for the first several runs

## Extra confirmation: traditional TA alongside the LLM panel

`signals/indicators.py` computes RSI(14), an SMA(10/30) trend read, and a
volatility reading from recent closes. These are always attached to the
signal JSON the LLM panel sees (`decisions.log` too), so the panel's
reasoning has that context. Set `REQUIRE_INDICATOR_CONFIRMATION=true` to
also make them a hard gate: a panel-approved buy is still rejected if RSI
>= 80 (overbought) or the SMA trend is down. Off by default.

## Adjusting risk limits

Edit `MAX_TRADE_PCT`, `DAILY_LOSS_HALT_PCT`, `MAX_CONSECUTIVE_LOSSES` in
`.env`. Defaults: max 20% of pool per trade, halt at -10% daily drawdown,
halt after 3 consecutive losing trades. A halt persists across restarts —
clear it manually via `CircuitBreaker(...).reset()` (or delete the relevant
`state/breaker_*.json` file) once you've reviewed why it fired.

## Editing the stock basket / crypto universe

`config/universe.py` — plain lists, no code changes needed elsewhere.

**Daily watchlist override:** if `state/watchlist.json` exists (a plain
JSON array of ticker strings, e.g. `["AAOI", "RGTI", "MU"]`), it replaces
`ROBOTICS_BASKET` for the stocks leg — resolved fresh on every scheduled
run, so a new file can be dropped each morning with no code changes and
no restart needed. Falls back to `ROBOTICS_BASKET` automatically if the
file is missing, malformed, or every entry fails ticker validation
(`^[A-Z][A-Z.\-]{0,5}$`, max 25 symbols). Only ticker symbols are ever
read from a watchlist file — prose/analysis/news text must never be
parsed out and fed to the LLM panel (see `llm-trading-agent-security`);
`signals/insider_signal.py` below is how numeric context from that kind
of source gets in safely.

**`scripts/update_watchlist.py`** generates that file automatically,
once a day, from real market data — no more manually pasting a briefing.
Fully deterministic, no LLM calls: pulls Yahoo Finance's "Day Gainers" /
"Small Cap Gainers" / "Aggressive Small Caps" / "Most Actives" screeners
via `yfinance`, filters out illiquid/penny names, scores the rest by
momentum moderated by RSI/SMA (same overbought caution as the crypto leg)
plus real insider-buying/analyst-upside signal, and writes the top 20 to
`state/watchlist.json`. On any failure it leaves the existing file
untouched rather than wiping it, and logs `watchlist_update_error` to
`decisions.log` so a broken run is visible, not silent. Scheduled as
**`TradingAgentWatchlist`**, daily at 16:30 local (after NYSE close in
both DST states, well before the `TradingAgentPaper` task's first scan
the next morning) via:

```
schtasks /Create /TN "TradingAgentWatchlist" /TR "\"...\pythonw.exe\" ...\scripts\update_watchlist.py" /SC DAILY /ST 16:30 /ED 12/31/2099 /RL LIMITED
```

Run `.venv\Scripts\python -m scripts.update_watchlist --dry-run` any
time to preview the next watchlist without writing it.

## Insider-trading and analyst-consensus context

`signals/insider_signal.py` pulls each stock candidate's SEC Form 4
insider-transaction history and analyst price-target/recommendation data
from yfinance (already a dependency, no new install) and always attaches
it to the signal JSON the LLM panel sees, alongside the RSI/SMA
indicators — advisory only, same as those, never a hard gate. Only
numeric fields (`net_usd` insider buy/sell, `days_since_last_trade`,
analyst `upside_pct`/`bullish_pct`) are included; the freeform `Text`
column from yfinance is used only to classify transactions in Python and
is never forwarded to the panel. A failure here (schema change, network
error) is logged to `decisions.log` as `insider_signal_error` rather than
silently disappearing — it can never block a review from happening.

## Tuning the Darvas box parameters

`signals/darvas.py::compute_box` defaults: 10-day box window, box must be
within 12% of high-to-low, box top must sit within 5% of the prior period
high, breakout requires 1.5x average box volume. Loosen `max_box_width_pct`
to catch more (noisier) boxes, raise `volume_multiplier` to demand a
stronger confirmation before buying.

## Cómo se decide qué comprar: descubrimiento por asimetría

El pipeline es `DESCUBRIR → PUNTUAR → RANKEAR → REVISAR → DIMENSIONAR →
EJECUTAR → MONITOREAR → SALIR`, y reemplaza a un embudo de filtros
booleanos que colapsaba a un único candidato por ciclo.

Qué medía el sistema anterior, sobre datos reales: de **243 pares** de OKX
llegaba **1** al panel de decisión, y de **20 acciones** también 1. En
acciones el rechazo era del 100% porque el watchlist se llena con los
mayores movers del día (ancho de rango promedio 24%) mientras la caja de
Darvas exige consolidación estrecha (≤12%): buscaba donde su propia
estrategia no podía encontrar nada. Cero operaciones en toda su historia.

Las piezas:

- **`signals/asymmetry.py`** — expectativa matemática por activo. Objetivo
  y stop se leen de la estructura real del precio (resistencia y soporte
  recientes), y la probabilidad de acierto se **mide** sobre la historia
  del propio activo con el método de doble barrera: para cada punto de
  entrada pasado, ¿tocó primero el objetivo o el stop? Se reporta siempre
  con `sample_size`, y la limitación de ventanas solapadas está
  documentada en el módulo en vez de escondida.
- **`signals/opportunity_scanner.py`** — puntúa **todo** el universo y lo
  rankea. Los filtros ahora **puntúan en vez de eliminar**: un RSI de 71
  baja el score, no descalifica. Solo se rechaza de plano lo que no se
  puede medir o tiene esperanza negativa, y **cada rechazo queda
  registrado con su motivo y su valor numérico**.
- **`execution/sizing.py`** — tamaño por convicción y bucket
  (`core` 10% / `momentum` 3% / `moonshot` 0,5%), escalado linealmente por
  la convicción. Antes toda operación era `pool_value * max_trade_pct`, es
  decir **siempre el máximo permitido**: 20% del pool, $200.000 con $1M en
  cuenta. Eso hacía inexpresable una apuesta especulativa pequeña — y
  explicaba la cautela del panel, porque cada "sí" costaba el 20%.
  `SpendGuard` sigue imponiendo `max_trade_pct` como techo duro: ningún
  tamaño que produce este módulo lo supera.
- **`signals/stock_strategies.py`** — entradas que sí encajan con activos
  en movimiento (`range_breakout`, `momentum_continuation`,
  `gap_continuation`, `capitulation_reversal`). Darvas sigue existiendo
  como una estrategia más, no como el único portón.
- **`signals/crypto_fundamentals.py`** — capitalización, FDV y supply
  desde la API gratuita de CoinGecko, para clasificar large/mid/small/micro
  y decidir el bucket de riesgo. Una micro cap va a `moonshot` aunque la
  señal se vea inmejorable: ahí el riesgo es no poder salir, y eso no lo
  arregla tener razón sobre la dirección.

Se evalúan hasta **5 candidatos por ciclo** (`MAX_CANDIDATES_PER_CYCLE`),
no 1. El tope existe porque cada revisión cuesta 3 llamadas a modelos con
cuota diaria.

### Alerta de parálisis

Cuando un ciclo no ejecuta nada, se registra `capital_deployment_alert`
distinguiendo dos situaciones que antes se veían idénticas (ambas eran
silencio):

- `NO_OPPORTUNITIES_FOUND` — ningún activo del universo tuvo esperanza
  positiva. Es una respuesta legítima del sistema.
- `SYSTEM_FAILED_TO_DEPLOY` — había candidatos con esperanza positiva y
  aun así no se ejecutó ninguno. Eso apunta al umbral del panel, al
  sizing o a los guards, y hay que ir a mirar.

## News sentiment (stocks)

`signals/news_sentiment.py` pulls each candidate's recent Yahoo Finance
news (via `yfinance`, free, already a dependency) and scores it with a
plain keyword count (bullish/bearish financial-press vocabulary) — no
LLM in the loop for this at all. Only bounded numbers reach the panel:
`sentiment_score` (-1 to 1), `article_count`, `mover_mentions` (how many
recent headlines mention a configured market-moving public figure —
currently Musk, Trump, Buffett, Powell, see `_MOVER_NAMES`), and
`hours_since_latest`. Raw headline/summary text is read and discarded
inside this one file and never concatenated into the LLM panel's prompt
— see `llm-trading-agent-security`: this is what that skill calls
"external data sanitized before entering the LLM context," done by
never letting the text enter at all rather than trying to sanitize it
after the fact. Always on (like the insider/analyst signal above), same
visible-not-silent failure logging (`news_sentiment_error`).

This does **not** cover real-time social media (e.g. reading Musk's or
Trump's own posts on X directly) — the X API no longer has a usable free
tier (paid plans start around $100+/month). What it covers instead is
financial-press coverage of such figures' market-moving statements/
actions once reported, which is free and already licensed by Yahoo
Finance. If real-time social monitoring is wanted later, that's a
separate, paid integration (X API, or a news-aggregator API like
Finnhub/Alpha Vantage that has its own free tier) — not built here.

## Backtesting: "what would this actually do?"

Replays the real strategy functions over historical data — no LLM calls,
no live orders, so it's safe to run any time and free (no API cost):

```
.venv\Scripts\python -m backtest.run_stocks_backtest --period 2y
.venv\Scripts\python -m backtest.run_crypto_backtest --days 90
```

Results land in `backtests/` (gitignored) as JSON. These are an **upper
bound** — the live LLM panel only ever reduces how many of these signals
actually execute, never adds to them. Crypto's result is a signal-frequency
count, not a profit/loss backtest, because `run_crypto()` has no coded
exit rule today (it only ever buys) — there's no exit strategy to replay.

## Smoke-testing order execution

To watch a real (fake-money) order, position record, and stop-loss exit
fire end-to-end without waiting for an organic signal:

```
.venv\Scripts\python -m scripts.smoke_test_order --broker okx --symbol BTC-USDT --confirm
.venv\Scripts\python -m scripts.smoke_test_order --broker okx --symbol BTC-USDT --close --confirm
```

(`--broker ibkr --symbol NVDA` works the same way, needs IB Gateway
logged into paper.) Refuses to run outside `--mode paper`/OKX Demo
Trading, and does nothing without `--confirm` (dry-run by default). Uses
an isolated `smoketest` pool for its risk-guard/position state — it can
never touch or trip the real `stocks`/`crypto` pools' budgets.

## Optional: Kronos forecast signal

`signals/kronos_forecast.py` adds a short-horizon price forecast (via a
small vendored open-source model, `vendor/kronos/`) to the signal JSON
the LLM panel sees — advisory only, same as the RSI/SMA indicators above,
never a hard gate. Off by default; needs a heavier, separate install:

```
.venv\Scripts\pip install -r requirements-kronos.txt
```

Then set `ENABLE_KRONOS_FORECAST=true` in `.env`. First run downloads
~small model weights from Hugging Face Hub and caches them locally;
after that, one forecast takes a few seconds on CPU — comfortably inside
the 30-60min scheduled-run budget.
