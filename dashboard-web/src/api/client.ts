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
export type RawLog = Record<string, unknown>;

async function getJSON<T>(path: string): Promise<T> {
  const res = await fetch(path);
  if (!res.ok) throw new Error(`${path} -> ${res.status}`);
  return res.json() as Promise<T>;
}

async function postJSON<T>(path: string): Promise<T> {
  const res = await fetch(path, { method: "POST" });
  if (!res.ok) throw new Error(`${path} -> ${res.status}`);
  return res.json() as Promise<T>;
}

export const api = {
  data: () => getJSON<DashboardData>("/data"),
  botStatus: () => getJSON<TaskStatusResponse>("/bot-status"),
  botStart: () => postJSON<TaskStatusResponse>("/bot-start"),
  botStop: () => postJSON<TaskStatusResponse>("/bot-stop"),
  marketRadar: () => getJSON<MarketRadar>("/market-radar"),
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
