import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

ROOT_DIR = Path(__file__).resolve().parent.parent
load_dotenv(ROOT_DIR / ".env")


@dataclass(frozen=True)
class Settings:
    mode: str  # "paper" or "live"

    ibkr_host: str
    ibkr_port: int
    ibkr_client_id: int

    okx_api_key: str
    okx_api_secret: str
    okx_api_passphrase: str
    okx_demo_flag: str  # "1" = demo trading, "0" = live, per OKX API convention

    gemini_api_key: str
    nvidia_api_key: str

    max_trade_pct: float
    daily_loss_halt_pct: float
    max_consecutive_losses: int
    require_indicator_confirmation: bool

    state_dir: Path
    logs_dir: Path


def load_settings() -> Settings:
    mode = os.environ.get("TRADING_MODE", "paper").lower()
    if mode not in ("paper", "live"):
        raise ValueError(f"TRADING_MODE must be 'paper' or 'live', got {mode!r}")

    is_paper = mode == "paper"

    ibkr_port = int(os.environ.get("IBKR_PAPER_PORT" if is_paper else "IBKR_LIVE_PORT", 0))

    okx_key_prefix = "OKX_DEMO_" if is_paper else "OKX_"

    return Settings(
        mode=mode,
        ibkr_host=os.environ.get("IBKR_HOST", "127.0.0.1"),
        ibkr_port=ibkr_port,
        ibkr_client_id=int(os.environ.get("IBKR_CLIENT_ID", 1)),
        okx_api_key=os.environ.get(f"{okx_key_prefix}API_KEY", ""),
        okx_api_secret=os.environ.get(f"{okx_key_prefix}API_SECRET", ""),
        okx_api_passphrase=os.environ.get(f"{okx_key_prefix}API_PASSPHRASE", ""),
        okx_demo_flag="1" if is_paper else "0",
        gemini_api_key=os.environ.get("GEMINI_API_KEY", ""),
        nvidia_api_key=os.environ.get("NVIDIA_API_KEY", ""),
        max_trade_pct=float(os.environ.get("MAX_TRADE_PCT", 0.20)),
        daily_loss_halt_pct=float(os.environ.get("DAILY_LOSS_HALT_PCT", 0.10)),
        max_consecutive_losses=int(os.environ.get("MAX_CONSECUTIVE_LOSSES", 3)),
        require_indicator_confirmation=os.environ.get("REQUIRE_INDICATOR_CONFIRMATION", "false").lower() in ("1", "true", "yes"),
        state_dir=ROOT_DIR / "state",
        logs_dir=ROOT_DIR / "logs",
    )
