"""Contrato tipado de /data, /bot-status, /bot-start y /bot-stop.

Estricto donde build_data() calcula la forma (portfolio, posiciones,
riesgo, standings...) y deliberadamente laxo (dict crudo) donde el dato
es un pase directo de decisions.log/trades.log escrito por
execution/orchestrator.py y signals/opportunity_scanner.py -- ahi la
forma real cambia entre entradas (confirmado en vivo: una entrada
`discovery.stocks` de hoy no trae `rejected_symbols`, la de `crypto` si,
porque se logueo antes de que ese campo existiera). Forzar esos bloques
a un modelo estricto no los haria mas seguros, solo mas fragiles --
generarian error de validacion ante el primer log viejo o el primer
campo nuevo, en dos sistemas que ni siquiera comparten deploy.

Este archivo es el unico lugar donde se define la forma que ve React:
FastAPI genera el OpenAPI spec desde estos modelos, y
`openapi-typescript` genera los tipos de TS desde ese spec. Cambiar un
campo aca y no correr el generador es la version moderna del bug que ya
paso dos veces con el dashboard viejo (`signal.indicators.rsi_14` ->
`signal.rsi_14` rompiendo el JS sin avisar) -- con este contrato, el
mismo cambio rompe la build de TypeScript en vez de romper en silencio.
"""
from typing import Any

from pydantic import BaseModel

RawLog = dict[str, Any]


class PoolHealth(BaseModel):
    last_result: str | None
    last_timestamp: str | None
    age_minutes: float | None
    stale: bool


class Position(BaseModel):
    entry_price: float
    qty: float
    stop: float


class Asymmetry(BaseModel):
    target_pct: float
    stop_pct: float
    reward_risk: float
    win_prob: float
    expected_value_pct: float
    volatility_pct: float
    sample_size: int
    resolved_pct: float


class LivePosition(BaseModel):
    pool: str
    symbol: str
    entry_price: float
    current_price: float | None
    qty: float
    stop: float
    market_value: float
    pnl_usd: float | None
    pnl_pct: float | None
    risk_usd: float
    opened_at: str | None
    strategy: str | None
    bucket: str | None
    confidence: float | None
    reasoning: str | None
    asymmetry: Asymmetry | None
    opportunity_score: float | None


class Breaker(BaseModel):
    date: str
    day_start_value: float
    consecutive_losses: int
    halted: bool
    halt_reason: str | None


class Spend(BaseModel):
    date: str
    spent: float


class Standing(BaseModel):
    strategy: str
    scored: int
    open_proposals: int
    win_rate: float | None
    avg_return_pct: float | None
    best_pct: float | None
    worst_pct: float | None


class StrategyPerformance(Standing):
    status: str  # "scored" | "awaiting_results"


class WatchlistEntry(BaseModel):
    symbol: str
    change_pct: float
    rsi_14: float
    sma_trend: str
    insider_net_usd: float
    analyst_upside_pct: float | None
    score: float


class WatchlistStatus(BaseModel):
    symbols: list[str]
    detail: list[WatchlistEntry]
    last_updated: str | None
    age_minutes: float | None
    stale: bool
    last_error: str | None


class PanelSentiment(BaseModel):
    buy_pct: float
    sample: int


class DeploymentAlert(BaseModel):
    timestamp: str
    age_minutes: float
    diagnosis: str | None
    detail: str | None
    assets_scanned: int | None
    shortlisted: int | None


class Portfolio(BaseModel):
    total_capital: float | None
    deployed_capital: float
    cash_pct: float | None
    n_positions: int
    buckets: dict[str, float]


class PortfolioPoint(BaseModel):
    timestamp: str
    total_value: float


class Performance(BaseModel):
    available: bool
    points: int
    reason: str | None = None
    total_return_pct: float | None = None
    max_drawdown_pct: float | None = None
    volatility_pct: float | None = None


class RiskAlert(BaseModel):
    level: str  # "warning" | "info"
    code: str
    detail: str


class LargestPosition(BaseModel):
    symbol: str
    pool: str
    market_value: float


class RiskCenter(BaseModel):
    total_capital: float | None
    exposure_pct: float | None
    exposure_by_pool: dict[str, float]
    bucket_exposure: dict[str, float]
    open_risk_usd: float
    largest_position: LargestPosition | None
    n_positions: int
    max_open_positions: int
    max_deployed_pct: float
    spend_today: dict[str, Spend]
    alerts: list[RiskAlert]


class ClosedTrade(BaseModel):
    pool: str
    symbol: str
    exit_price: float | None
    stop: float | None
    won: bool | None
    closed_at: str
    opened_at: str | None
    bucket: str | None
    position_usd: float | None
    reasoning: str | None


class DashboardData(BaseModel):
    generated_at: str
    pools: dict[str, PoolHealth]
    positions: dict[str, dict[str, Position]]
    live_positions: list[LivePosition]
    breakers: dict[str, Breaker]
    spend: dict[str, Spend]
    standings: list[Standing]
    strategy_performance: list[StrategyPerformance]
    watchlist: WatchlistStatus
    last_scan: RawLog | None
    panel_sentiment: PanelSentiment | None
    discovery: dict[str, RawLog | None]
    rejection_totals: dict[str, int]
    deployment_alerts: dict[str, DeploymentAlert]
    portfolio: Portfolio
    portfolio_series: list[PortfolioPoint]
    performance: Performance
    risk: RiskCenter
    closed_trades: list[ClosedTrade]
    error_count: int
    last_error: RawLog | None
    recent_decisions: list[RawLog]
    recent_trades: list[RawLog]


class TaskStatusResponse(BaseModel):
    tasks: dict[str, str]


class KillSwitchStatus(BaseModel):
    """None = ese pool puede abrir posiciones; string = motivo del corte."""
    stocks_blocked: str | None
    crypto_blocked: str | None


class KillSwitchRequest(BaseModel):
    reason: str | None = None
    pools: list[str] | None = None


class Mover(BaseModel):
    symbol: str
    exchange: str
    price: float
    change_24h_pct: float
    volume_24h_usd: float
    suspicious: bool


class DexMover(Mover):
    blockchain: str


class MarketRadar(BaseModel):
    generated_at: str
    cex_movers: list[Mover]
    dex_movers: list[DexMover]
