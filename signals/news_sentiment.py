"""Deterministic, keyword-based news sentiment for the stocks leg, sourced
from yfinance's Yahoo Finance news feed (free, no API key, already a
project dependency).

Security note (see llm-trading-agent-security): this is the "isolated
text-to-number reduction step" flagged as needed in insider_signal.py's
docstring -- done the safest possible way, with NO LLM in the loop at
all. Headline/summary text is scored by plain keyword counting in this
file and then discarded; only bounded numeric fields (sentiment_score,
article_count, mover_mentions, hours_since_latest) ever leave this
module and reach the trading LLM panel's prompt. There is no
execution-capable model here for an adversarial headline to target --
the worst a hostile headline can do is nudge a keyword count, which is
already clamped to [-1, 1] and is just one advisory input among several
(indicators, insider data, analyst data) the panel weighs. It cannot
place an order or override a risk gate on its own.

This does NOT cover real-time social media (e.g. what Elon Musk or
Donald Trump post on X) -- that needs the X API, which no longer has a
usable free tier. What this covers is financial press coverage of such
figures' market-moving actions/statements once it's been reported,
which is free, structured, and already licensed by Yahoo Finance.
"""
import re
from dataclasses import dataclass
from datetime import datetime, timezone

import yfinance as yf

# Word-boundary matched, case-insensitive. Deliberately plain financial-
# press vocabulary, not exhaustive -- this is a coarse directional signal
# for the panel to weigh, not a standalone trading decision.
_BULLISH_WORDS = [
    "surge", "surges", "soar", "soars", "rally", "rallies", "beat", "beats",
    "upgrade", "upgraded", "outperform", "breakthrough", "bullish", "jump", "jumps",
    "record high", "all-time high", "approval", "approved", "raised guidance",
    "raises guidance", "strong demand",
]
_BEARISH_WORDS = [
    "plunge", "plunges", "crash", "crashes", "downgrade", "downgraded", "miss", "misses",
    "bearish", "selloff", "sell-off", "lawsuit", "investigation", "recall", "layoffs",
    "layoff", "bankruptcy", "fraud", "probe", "cut guidance", "cuts guidance",
    "warning", "warns", "plummet", "plummets", "tumble", "tumbles",
]

# Public figures whose statements/investment moves are well-documented to
# swing specific stocks. Counted separately so the panel can see "N
# recent headlines mention a market-moving figure" -- never what that
# figure actually said, which never leaves this file.
_MOVER_NAMES = [
    "elon musk", "musk",
    "donald trump", "trump",
    "warren buffett", "buffett",
    "jerome powell", "powell",
]

_PATTERN_CACHE: dict[str, re.Pattern] = {}


def _count_matches(text: str, phrases: list[str]) -> int:
    total = 0
    for phrase in phrases:
        pattern = _PATTERN_CACHE.get(phrase)
        if pattern is None:
            pattern = re.compile(r"\b" + re.escape(phrase) + r"\b", re.IGNORECASE)
            _PATTERN_CACHE[phrase] = pattern
        total += len(pattern.findall(text))
    return total


@dataclass
class NewsSentiment:
    sentiment_score: float  # -1.0 (bearish) to +1.0 (bullish); 0.0 if no signal either way
    article_count: int  # articles actually scored (within lookback_hours)
    mover_mentions: int  # of those, how many mention a configured market-moving figure
    hours_since_latest: float | None


def fetch_sentiment(symbol: str, max_articles: int = 10, lookback_hours: float = 72) -> NewsSentiment:
    """Raises on failure -- caller must catch and log, not swallow (same
    discipline as insider_signal.py: a silently-swallowed exception here
    would be indistinguishable from "no news today", the exact failure
    mode already chased down twice this session)."""
    articles = yf.Ticker(symbol).news or []
    now = datetime.now(timezone.utc)

    bull = bear = movers = kept = 0
    latest_ts = None
    for item in articles[:max_articles]:
        # yfinance has changed this shape before (flat vs. nested under
        # "content") -- tolerate both rather than assume one.
        c = item.get("content", item)
        title = c.get("title") or ""
        summary = c.get("summary") or c.get("description") or ""
        text = f"{title} {summary}"

        ts = None
        pub = c.get("pubDate") or c.get("providerPublishTime")
        if isinstance(pub, str):
            try:
                ts = datetime.fromisoformat(pub.replace("Z", "+00:00"))
            except ValueError:
                ts = None
        elif isinstance(pub, (int, float)):
            ts = datetime.fromtimestamp(pub, tz=timezone.utc)

        if ts is not None and (now - ts).total_seconds() > lookback_hours * 3600:
            continue  # too old to be current market context

        kept += 1
        bull += _count_matches(text, _BULLISH_WORDS)
        bear += _count_matches(text, _BEARISH_WORDS)
        movers += _count_matches(text, _MOVER_NAMES)
        if ts is not None and (latest_ts is None or ts > latest_ts):
            latest_ts = ts

    total = bull + bear
    score = 0.0 if total == 0 else round((bull - bear) / total, 3)
    hours_since = round((now - latest_ts).total_seconds() / 3600, 1) if latest_ts else None

    return NewsSentiment(
        sentiment_score=score,
        article_count=kept,
        mover_mentions=movers,
        hours_since_latest=hours_since,
    )
