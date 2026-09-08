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
  extra models (Llama, Mixtral, DeepSeek) alongside Gemini for the trade
  review vote — majority wins, ties/no-majority default to hold. Leave blank
  to use Gemini alone.
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
- **`TradingAgentCrypto`** — `--pool crypto`, daily, repeat every 60min,
  ~24h window

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

## Tuning the Darvas box parameters

`signals/darvas.py::compute_box` defaults: 10-day box window, box must be
within 12% of high-to-low, box top must sit within 5% of the prior period
high, breakout requires 1.5x average box volume. Loosen `max_box_width_pct`
to catch more (noisier) boxes, raise `volume_multiplier` to demand a
stronger confirmation before buying.

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
