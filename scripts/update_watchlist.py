"""Regenerates state/watchlist.json once a day from real market data,
replacing the manual "paste a briefing from another Claude session" step.

Fully deterministic -- no LLM calls here. Same division of labor as the
crypto leg's rank_crypto(): this module only ranks and narrows candidates;
the actual buy/hold decision for whichever symbol later triggers a Darvas
breakout still goes through the LLM panel in run_stocks(). This script
decides what to WATCH, never what to BUY.

Source data: yfinance's built-in Yahoo Finance screeners (yf.screen), the
same predefined "Day Gainers" / "Small Cap Gainers" lists Yahoo Finance's
own UI uses -- structured quote data, not scraped HTML or third-party
prose, so there's nothing here that needs the prompt-injection sanitizing
llm-trading-agent-security calls out (this script doesn't call the LLM
panel at all).

Run this once daily via Task Scheduler, well before the stocks leg's
first scan of the day -- see README.md.
"""
import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import yfinance as yf

from config.universe import WATCHLIST_PATH, _MAX_WATCHLIST_SIZE, _TICKER_RE
from signals.indicators import rsi, sma_trend
from signals.insider_signal import analyst_consensus, insider_sentiment

_SCREENERS = ["day_gainers", "small_cap_gainers", "aggressive_small_caps", "most_actives"]
_SCREENER_COUNT = 40  # per screener, before filtering
_MIN_PRICE = 3.0  # excludes penny/OTC-adjacent names that are thin or hard to route
_MIN_DAY_VOLUME = 200_000  # basic liquidity floor
_TOP_N = min(20, _MAX_WATCHLIST_SIZE)  # how many symbols end up in the watchlist
_MAX_SCORED = 35  # cap how many candidates get the (slower) per-symbol yfinance calls


def _log(logs_dir: Path, record: dict) -> None:
    from datetime import datetime, timezone
    logs_dir.mkdir(parents=True, exist_ok=True)
    record = {"timestamp": datetime.now(timezone.utc).isoformat(), **record}
    with open(logs_dir / "decisions.log", "a") as f:
        f.write(json.dumps(record) + "\n")


def _fetch_candidates() -> list[dict]:
    """Merged, de-duped quotes across all configured screeners. Raises on
    total failure -- caller decides what "no candidates" should mean."""
    seen = {}
    for screener in _SCREENERS:
        result = yf.screen(screener, count=_SCREENER_COUNT)
        for q in result.get("quotes", []):
            symbol = q.get("symbol", "")
            if symbol and symbol not in seen:
                seen[symbol] = q
    return list(seen.values())


def _passes_basic_filter(q: dict) -> bool:
    symbol = q.get("symbol", "")
    price = q.get("regularMarketPrice")
    volume = q.get("dayVolume") or q.get("regularMarketVolume")
    if not _TICKER_RE.match(symbol):
        return False
    if price is None or price < _MIN_PRICE:
        return False
    if volume is not None and volume < _MIN_DAY_VOLUME:
        return False
    return True


def _score(symbol: str, change_pct: float) -> tuple[float, dict]:
    """Deterministic composite score -- momentum is the primary driver
    (that's why it showed up on a gainers screener at all), moderated by
    the same overbought caution as the crypto leg's RSI walk, nudged by
    real insider/analyst signal. Returns (score, detail) so the log line
    shows the reasoning, not just a bare number."""
    hist = yf.Ticker(symbol).history(period="3mo")
    closes = hist["Close"].tolist()
    rsi_14 = rsi(closes)
    trend = sma_trend(closes)

    score = change_pct
    if rsi_14 is not None and rsi_14 >= 80:
        score *= 0.5  # already blown out -- same caution as run_crypto()'s RSI>=70 walk
    if trend == "down":
        score *= 0.5

    insider_bonus = analyst_bonus = 0.0
    insider = analyst = None
    try:
        insider = insider_sentiment(symbol)
        if insider.net_usd > 0:
            insider_bonus = 5.0
        elif insider.net_usd < 0 and insider.sell_count >= 3:
            insider_bonus = -5.0
    except Exception:
        pass  # per-symbol enrichment failure must not drop the candidate
    try:
        analyst = analyst_consensus(symbol)
        if analyst.upside_pct is not None:
            analyst_bonus = max(-5.0, min(5.0, analyst.upside_pct / 10))
    except Exception:
        pass
    score += insider_bonus + analyst_bonus

    return score, {
        "change_pct": round(change_pct, 2),
        "rsi_14": rsi_14,
        "sma_trend": trend,
        "insider_net_usd": insider.net_usd if insider else None,
        "analyst_upside_pct": analyst.upside_pct if analyst else None,
        "score": round(score, 2),
    }


def build_watchlist() -> tuple[list[str], list[dict]]:
    candidates = [q for q in _fetch_candidates() if _passes_basic_filter(q)]
    # Strongest movers first; only the top _MAX_SCORED get the slower
    # per-symbol history/insider/analyst calls.
    candidates.sort(key=lambda q: q.get("regularMarketChangePercent", 0), reverse=True)
    candidates = candidates[:_MAX_SCORED]

    scored = []
    for q in candidates:
        symbol = q["symbol"]
        try:
            score, detail = _score(symbol, q.get("regularMarketChangePercent", 0.0))
            scored.append((symbol, score, detail))
        except Exception:
            continue  # one bad symbol (delisted, no history) skips, doesn't abort the run

    scored.sort(key=lambda t: t[1], reverse=True)
    top = scored[:_TOP_N]
    symbols = [s for s, _, _ in top]
    details = [{"symbol": s, **d} for s, _, d in top]
    return symbols, details


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="print the result, don't write watchlist.json")
    args = parser.parse_args()

    logs_dir = Path(__file__).resolve().parent.parent / "logs"

    try:
        symbols, details = build_watchlist()
    except Exception as e:
        # Fail closed: never wipe a known-good watchlist because today's
        # screener call happened to fail (rate limit, Yahoo outage, etc).
        _log(logs_dir, {"pool": "stocks", "result": "watchlist_update_error", "reason": str(e)})
        print(f"watchlist update failed, leaving existing state/watchlist.json untouched: {e}", file=sys.stderr)
        sys.exit(1)

    if not symbols:
        _log(logs_dir, {"pool": "stocks", "result": "watchlist_update_error", "reason": "no candidates survived filtering"})
        print("no candidates survived filtering, leaving existing state/watchlist.json untouched", file=sys.stderr)
        sys.exit(1)

    print(json.dumps(details, indent=2))
    if args.dry_run:
        return

    WATCHLIST_PATH.parent.mkdir(parents=True, exist_ok=True)
    WATCHLIST_PATH.write_text(json.dumps(symbols), encoding="utf-8")
    _log(logs_dir, {"pool": "stocks", "result": "watchlist_updated", "symbols": symbols, "detail": details})
    print(f"wrote {len(symbols)} symbols to {WATCHLIST_PATH}")


if __name__ == "__main__":
    main()
