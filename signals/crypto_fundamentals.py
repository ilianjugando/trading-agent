"""Crypto fundamentals -- market cap, fully-diluted valuation, dilution
headroom, and relative liquidity -- from CoinGecko's free public API (no
key required), to classify assets by size and flag structural risk (heavy
future dilution, thin liquidity relative to market cap) for the crypto leg.

Security note (see llm-trading-agent-security): numeric-only, same
discipline as insider_signal.py / news_sentiment.py. No free-form text
ever leaves this module.

Rate limit note: the free tier throttles hard -- live testing during
development got a 429 on the 5th rapid call within ~2 seconds (no
documented per-minute number is published for the keyless tier, but
empirically it's low single digits per second at most, and bursts fail
fast). That makes a per-symbol call in a loop unusable for a ~42-pair
scan. This module instead fetches the top `per_page` coins by market cap
in ONE call and lets the caller cross-reference many OKX pairs against
that single result. Never call this API per-symbol in a loop.

Deliberately does NOT catch its own exceptions -- a broken response shape
or network failure must surface as a visible error, not silently look
identical to "no fundamentals data" (see insider_signal.py's docstring;
this project already lost real debugging time to that exact failure
mode). The caller is responsible for catching and logging.
"""
import json
import urllib.request
from dataclasses import dataclass

_MARKETS_URL = (
    "https://api.coingecko.com/api/v3/coins/markets"
    "?vs_currency=usd&order=market_cap_desc&per_page={per_page}&page=1"
)


@dataclass
class CryptoFundamentals:
    market_cap_usd: float
    fdv_usd: float | None  # None when CoinGecko can't compute it (no known max supply)
    circulating_supply_pct: float | None  # circulating/total*100 -- low = heavy future dilution overhang
    volume_to_mcap_ratio: float | None  # 24h volume / market cap -- relative liquidity
    size_bucket: str  # "large" | "mid" | "small" | "micro"
    rank: int | None  # CoinGecko global market cap rank


# USD market-cap cutoffs for size_bucket. These mirror where the market
# itself changes character, not an arbitrary split:
#   >= $10B  "large" -- mega-caps (BTC/ETH plus a handful of others):
#            deep multi-exchange liquidity, minimal single-listing risk.
#   >= $1B   "mid"   -- still broadly liquid and exchange-covered, but a
#            single exchange's order book can matter.
#   >= $100M "small" -- spreads widen noticeably, concentration in 1-2
#            exchanges is common, a moderate order can move price.
#   < $100M  "micro" -- thin enough that liquidity risk and delisting/
#            rug risk dominate any technical signal.
_LARGE_CUTOFF = 10_000_000_000
_MID_CUTOFF = 1_000_000_000
_SMALL_CUTOFF = 100_000_000


def _size_bucket(market_cap_usd: float) -> str:
    if market_cap_usd >= _LARGE_CUTOFF:
        return "large"
    if market_cap_usd >= _MID_CUTOFF:
        return "mid"
    if market_cap_usd >= _SMALL_CUTOFF:
        return "small"
    return "micro"


def okx_inst_id_to_symbol(inst_id: str) -> str:
    """"BTC-USDT" -> "BTC". Base asset is always the first segment of an
    OKX spot instId."""
    return inst_id.split("-")[0].upper()


def fetch_market_fundamentals(per_page: int = 250) -> dict[str, CryptoFundamentals]:
    """Top `per_page` coins by market cap (CoinGecko's hard cap is 250),
    in a single HTTP call, keyed by uppercased symbol. Raises on any
    failure -- caller must catch and log, not swallow.

    Coverage, measured live against OKX's own top-42-by-24h-volume USDT
    pairs (excluding stablecoin pairs): 34/42 (81%) had a match here.
    The 8 misses were either real altcoins ranked below 250 by market cap
    (e.g. IOST) or OKX's tokenized-stock products (X-prefixed instIds
    like XMSTR/XCRCL/XSOXL/XSNDK -- these aren't cryptocurrencies and
    were never going to be on CoinGecko's coin list). A caller that needs
    a symbol not present in the returned dict should treat that as "no
    fundamentals data available", not an error.

    CoinGecko carries multiple distinct projects under the same ticker
    (confirmed live: DAI, FRAX, AI, M and others each had 2+ rows in the
    top 1000 -- unrelated projects and/or scam clones of popular
    tickers). Rows come back sorted by market_cap_desc, so the first row
    seen for a given symbol is the largest by market cap; later
    duplicates are dropped as the near-certain irrelevant/scam copy.
    """
    url = _MARKETS_URL.format(per_page=per_page)
    req = urllib.request.Request(url, headers={"User-Agent": "trading-agent/1.0"})
    with urllib.request.urlopen(req, timeout=20) as resp:
        rows = json.loads(resp.read().decode("utf-8"))

    out: dict[str, CryptoFundamentals] = {}
    for row in rows:
        symbol = row["symbol"].upper()
        if symbol in out:
            continue  # lower-market-cap duplicate ticker -- see docstring

        market_cap = row.get("market_cap")
        if market_cap is None:
            continue  # can't classify or compute ratios without it
        market_cap = float(market_cap)

        fdv = row.get("fully_diluted_valuation")
        fdv = float(fdv) if fdv is not None else None

        circulating = row.get("circulating_supply")
        total = row.get("total_supply")
        circulating_pct = (
            round(float(circulating) / float(total) * 100, 2)
            if circulating is not None and total not in (None, 0)
            else None
        )

        volume = row.get("total_volume")
        volume_ratio = round(float(volume) / market_cap, 4) if volume is not None and market_cap > 0 else None

        rank = row.get("market_cap_rank")
        rank = int(rank) if rank is not None else None

        out[symbol] = CryptoFundamentals(
            market_cap_usd=market_cap,
            fdv_usd=fdv,
            circulating_supply_pct=circulating_pct,
            volume_to_mcap_ratio=volume_ratio,
            size_bucket=_size_bucket(market_cap),
            rank=rank,
        )
    return out
