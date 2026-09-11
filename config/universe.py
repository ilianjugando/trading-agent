# Editable watchlists. Add/remove symbols here — no code changes needed elsewhere.

# Example theme basket: robotics / automation supply chain.
# Mix of pure-plays and suppliers so the "trend" signal can catch the theme
# even when the flagship names (e.g. NVDA) are crowded/expensive.
ROBOTICS_BASKET = [
    # Verificado contra IBKR (2026-09-11): estos cotizan en SMART/USD.
    # Salieron IRBT (ahora IRBTQ, en quiebra) y ABB (sin listado en USD;
    # el ADR es ABBNY en PINK) -- IBKR devolvia error 200 para ambos, o
    # sea que una ruptura ahi no se podia ejecutar.
    "ISRG",   # Intuitive Surgical - surgical robotics
    "TER",    # Teradyne - owns Universal Robots
    "ROK",    # Rockwell Automation
    "FANUY",  # Fanuc ADR
    "PATH",   # UiPath - software robotics/RPA
    "NVDA",   # Nvidia - compute backbone
]

# Optional daily override: if state/watchlist.json exists and is valid, its
# ticker list replaces ROBOTICS_BASKET for the stocks leg -- lets an external
# routine (e.g. a daily briefing generator) point the bot at fresh symbols
# without touching code. Only the ticker strings are trusted from that file;
# any prose/analysis in a briefing must never be regexed out of it and fed
# to the LLM panel -- see llm-trading-agent-security. compute_box() still
# pulls each symbol's own OHLCV from yfinance independently.
import json
import re
from pathlib import Path

WATCHLIST_PATH = Path(__file__).resolve().parent.parent / "state" / "watchlist.json"
_TICKER_RE = re.compile(r"^[A-Z][A-Z.\-]{0,5}$")
_MAX_WATCHLIST_SIZE = 25


def resolve_stock_universe() -> list[str]:
    """ROBOTICS_BASKET is the fallback whenever the override file is
    missing, empty, malformed, or contains nothing that survives
    validation -- this must never raise, since a bad file should degrade
    to the known-good basket, not stop the stocks leg from running."""
    try:
        raw = json.loads(WATCHLIST_PATH.read_text(encoding="utf-8"))
        if not isinstance(raw, list):
            return ROBOTICS_BASKET
        symbols = [s.strip().upper() for s in raw if isinstance(s, str)]
        symbols = [s for s in symbols if _TICKER_RE.match(s)]
        # de-dupe, preserve order
        symbols = list(dict.fromkeys(symbols))[:_MAX_WATCHLIST_SIZE]
        return symbols or ROBOTICS_BASKET
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return ROBOTICS_BASKET

# Crypto universe to scan for momentum, as OKX instrument IDs (spot).
CRYPTO_UNIVERSE = [
    "BTC-USDT",
    "ETH-USDT",
    "SOL-USDT",
    "AVAX-USDT",
    "LINK-USDT",
]
