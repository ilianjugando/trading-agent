"""Insider-trading and analyst-consensus context for the stocks leg,
sourced from yfinance's structured SEC Form 4 / analyst-coverage data.

Security note (see llm-trading-agent-security): only numeric/categorical
fields ever reach the LLM panel. yfinance's insider `Text` column is
Yahoo's auto-generated boilerplate ("Sale at price 227.70 - 234.00 per
share.") from a regulated filing, not third-party free text -- but even
so it is used here only as a Python string-prefix classifier, and its
raw value is never included in what gets returned. If a real news/social
sentiment signal is ever wanted, that needs its own isolated
text-to-number reduction step, not a shortcut through this file.

Deliberately does NOT catch its own exceptions (unlike llm_review.py's
per-model fail-safe) -- a broken column name or pandas API mismatch here
must surface as a visible error, not silently look identical to "no
insider activity". This project already lost real time twice this
session (the dead 4-model LLM panel, the 20/day Gemini quota) to
failures a fail-safe swallowed into something indistinguishable from a
legitimate result. The caller (orchestrator.py) is responsible for
catching and logging, the same way it already treats tournament errors
as non-fatal-but-visible.
"""
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import yfinance as yf

_SELL_PREFIXES = ("Sale",)
# "Option Exercise" and stock awards/gifts are routine compensation
# mechanics (exercise is frequently paired with an immediate sale), not a
# market view -- only an open-market Purchase counts as bullish insider
# signal, same discipline as excluding awards/gifts from the sell side.
_BUY_PREFIXES = ("Purchase",)


@dataclass
class InsiderSignal:
    net_usd: float  # buys minus sells, trailing window
    sell_count: int
    buy_count: int
    days_since_last_trade: float | None


@dataclass
class AnalystSignal:
    upside_pct: float | None  # (mean target - current) / current * 100
    bullish_pct: float | None  # share of strongBuy+buy among all ratings
    analyst_count: int | None


def insider_sentiment(symbol: str, window_days: int = 30) -> InsiderSignal:
    """Raises on any failure -- caller must catch and log, not swallow."""
    import pandas as pd

    df = yf.Ticker(symbol).insider_transactions
    if df is None or df.empty:
        return InsiderSignal(net_usd=0.0, sell_count=0, buy_count=0, days_since_last_trade=None)

    cutoff = datetime.now(timezone.utc) - timedelta(days=window_days)
    dates = pd.to_datetime(df["Start Date"], utc=True, errors="coerce")
    recent = df[dates >= cutoff]

    sells = recent[recent["Text"].str.startswith(_SELL_PREFIXES, na=False)]
    buys = recent[recent["Text"].str.startswith(_BUY_PREFIXES, na=False)]
    net_usd = float(buys["Value"].sum()) - float(sells["Value"].sum())

    all_dates = dates.dropna()
    days_since = (datetime.now(timezone.utc) - all_dates.max()).total_seconds() / 86400 if len(all_dates) else None

    return InsiderSignal(
        net_usd=round(net_usd, 2),
        sell_count=len(sells),
        buy_count=len(buys),
        days_since_last_trade=round(days_since, 1) if days_since is not None else None,
    )


def analyst_consensus(symbol: str) -> AnalystSignal:
    """Raises on any failure -- caller must catch and log, not swallow."""
    ticker = yf.Ticker(symbol)
    pt = ticker.analyst_price_targets
    upside_pct = None
    if pt and pt.get("current") and pt.get("mean"):
        upside_pct = round((pt["mean"] - pt["current"]) / pt["current"] * 100, 1)

    bullish_pct = analyst_count = None
    rec = ticker.recommendations
    if rec is not None and not rec.empty:
        row = rec[rec["period"] == "0m"].iloc[0] if (rec["period"] == "0m").any() else rec.iloc[0]
        total = int(row[["strongBuy", "buy", "hold", "sell", "strongSell"]].sum())
        if total > 0:
            # float() first: pandas/numpy scalars aren't JSON-serializable,
            # and this dict ends up straight in json.dumps() for the prompt.
            bullish_pct = round(float(row["strongBuy"] + row["buy"]) / total * 100, 1)
            analyst_count = total

    return AnalystSignal(upside_pct=upside_pct, bullish_pct=bullish_pct, analyst_count=analyst_count)
