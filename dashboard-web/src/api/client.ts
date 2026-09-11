import type { components } from "./schema";

export type DashboardData = components["schemas"]["DashboardData"];
export type TaskStatusResponse = components["schemas"]["TaskStatusResponse"];
export type LivePosition = components["schemas"]["LivePosition"];
export type Standing = components["schemas"]["Standing"];
export type StrategyPerformance = components["schemas"]["StrategyPerformance"];
export type ClosedTrade = components["schemas"]["ClosedTrade"];
export type RiskAlert = components["schemas"]["RiskAlert"];
export type WatchlistEntry = components["schemas"]["WatchlistEntry"];
export type PortfolioPoint = components["schemas"]["PortfolioPoint"];
export type Mover = components["schemas"]["Mover"];
export type DexMover = components["schemas"]["DexMover"];
export type MarketRadar = components["schemas"]["MarketRadar"];
export type BacktestRun = components["schemas"]["BacktestRun"];
export type CustomStrategy = components["schemas"]["CustomStrategy"];
export type CustomStrategyList = components["schemas"]["CustomStrategyList"];
export type StrategyRule = components["schemas"]["StrategyRule"];
export type RawLog = Record<string, unknown>;

async function getJSON<T>(path: string): Promise<T> {
  const res = await fetch(path);
  if (!res.ok) throw new Error(`${path} -> ${res.status}`);
  return res.json() as Promise<T>;
}

async function postJSON<T>(path: string, body?: unknown): Promise<T> {
  const res = await fetch(path, {
    method: "POST",
    headers: body ? { "Content-Type": "application/json" } : undefined,
    body: body ? JSON.stringify(body) : undefined,
  });
  if (!res.ok) throw new Error(((await res.json().catch(() => null)) as { detail?: string } | null)?.detail ?? `${path} -> ${res.status}`);
  return res.json() as Promise<T>;
}

export const api = {
  data: () => getJSON<DashboardData>("/data"),
  botStatus: () => getJSON<TaskStatusResponse>("/bot-status"),
  botStart: () => postJSON<TaskStatusResponse>("/bot-start"),
  botStop: () => postJSON<TaskStatusResponse>("/bot-stop"),
  marketRadar: () => getJSON<MarketRadar>("/market-radar"),
  customStrategies: () => getJSON<CustomStrategyList>("/strategies/custom"),
  saveStrategy: (s: CustomStrategy) => postJSON<CustomStrategyList>("/strategies/custom", s),
  deleteStrategy: async (name: string) => {
    const res = await fetch(`/strategies/custom/${encodeURIComponent(name)}`, { method: "DELETE" });
    if (!res.ok) throw new Error(`delete -> ${res.status}`);
    return res.json() as Promise<CustomStrategyList>;
  },
  backtest: (symbol: string, strategy: string, period: string) =>
    getJSON<BacktestRun>(`/backtest?symbol=${encodeURIComponent(symbol)}&strategy=${encodeURIComponent(strategy)}&period=${encodeURIComponent(period)}`),
};

/** Un pool en discovery/scan es un pase directo de decisions.log, forma
 * no garantizada (ver api/schemas.py) -- se leen sus campos de a uno,
 * nunca se asume la forma completa. */
export interface DiscoveryEntry {
  timestamp?: string;
  assets_scanned?: number;
  assets_shortlisted?: number;
  assets_rejected?: number;
  rejection_reasons?: Record<string, number>;
  rejected_symbols?: Record<string, string[]>;
  top_candidates?: { symbol: string; score: number; ev_pct: number | null }[];
}
