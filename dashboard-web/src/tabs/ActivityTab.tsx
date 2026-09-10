import type { DashboardData, RawLog } from "../api/client";
import { Card, Chip, EmptyState, SectionLabel, Table, Td, Th } from "../components/primitives";
import { POOL_LABELS, relativeTime } from "../lib/format";

const RESULT_TONE: Record<string, "ok" | "bad" | "warn" | "muted"> = {
  executed: "ok",
  skipped: "warn",
  skipped_sizing: "warn",
  skipped_portfolio_full: "warn",
  skipped_capital_reserve: "warn",
  capital_deployment_alert: "warn",
  order_rejected: "bad",
  order_not_filled: "bad",
  error: "bad",
  halted: "bad",
  rejected_by_spend_guard: "bad",
  stopped_out: "bad",
};

export function ActivityTab({ data }: { data: DashboardData }) {
  const { pools, watchlist, panel_sentiment, recent_decisions } = data;

  return (
    <div className="flex flex-col gap-4">
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
        {(["stocks", "crypto"] as const).map((pool) => {
          const h = pools[pool];
          return (
            <Card key={pool}>
              <SectionLabel>{POOL_LABELS[pool] ?? pool}</SectionLabel>
              <div className="flex items-center gap-2">
                <Chip tone={h.stale ? "warn" : "ok"}>{h.stale ? "Posiblemente atascado" : "Al día"}</Chip>
              </div>
              <div className="mt-2 text-sm">{h.last_result ?? "sin datos"}</div>
              <div className="text-xs text-muted">{relativeTime(h.age_minutes)}</div>
            </Card>
          );
        })}

        <Card>
          <SectionLabel>Watchlist</SectionLabel>
          <div className="font-mono text-2xl font-semibold">{watchlist.symbols.length}</div>
          <div className="text-xs text-muted">
            {watchlist.stale ? "desactualizada" : "al día"} · {relativeTime(watchlist.age_minutes)}
          </div>
          {watchlist.last_error && <div className="mt-1 text-xs text-bad">{watchlist.last_error}</div>}
        </Card>

        <Card>
          <SectionLabel>Sentimiento del panel</SectionLabel>
          {panel_sentiment ? (
            <>
              <div className="font-mono text-2xl font-semibold text-ai">{panel_sentiment.buy_pct}%</div>
              <div className="text-xs text-muted">votos de compra · últimas {panel_sentiment.sample} decisiones</div>
            </>
          ) : (
            <div className="text-sm text-muted">Sin datos todavía.</div>
          )}
        </Card>
      </div>

      <Card>
        <SectionLabel>Watchlist actual</SectionLabel>
        {watchlist.detail.length === 0 ? (
          <EmptyState>Sin watchlist generada todavía.</EmptyState>
        ) : (
          <Table>
            <thead>
              <tr>
                <Th>Símbolo</Th>
                <Th align="right">Cambio</Th>
                <Th align="right">RSI</Th>
                <Th>Tendencia</Th>
                <Th align="right">Score</Th>
              </tr>
            </thead>
            <tbody>
              {watchlist.detail.map((w) => (
                <tr key={w.symbol}>
                  <Td className="font-mono">{w.symbol}</Td>
                  <Td align="right" className={`font-mono ${w.change_pct >= 0 ? "text-accent" : "text-bad"}`}>{w.change_pct.toFixed(1)}%</Td>
                  <Td align="right" className="font-mono">{w.rsi_14.toFixed(1)}</Td>
                  <Td>{w.sma_trend}</Td>
                  <Td align="right" className="font-mono text-ai">{w.score.toFixed(1)}</Td>
                </tr>
              ))}
            </tbody>
          </Table>
        )}
      </Card>

      <Card>
        <SectionLabel>Decisiones recientes</SectionLabel>
        {recent_decisions.length === 0 ? (
          <EmptyState>Sin actividad reciente.</EmptyState>
        ) : (
          <Table>
            <thead>
              <tr>
                <Th>Hora</Th>
                <Th>Pool</Th>
                <Th>Símbolo</Th>
                <Th>Resultado</Th>
              </tr>
            </thead>
            <tbody>
              {recent_decisions.map((d, i) => {
                const raw = d as RawLog;
                const signal = raw.signal as RawLog | undefined;
                const symbol = (signal?.symbol as string | undefined) ?? (raw.symbol as string | undefined) ?? "—";
                const result = String(raw.result ?? "");
                return (
                  <tr key={i}>
                    <Td className="font-mono text-xs">{raw.timestamp ? new Date(String(raw.timestamp)).toLocaleString("es-AR") : "—"}</Td>
                    <Td>{POOL_LABELS[String(raw.pool)] ?? String(raw.pool ?? "—")}</Td>
                    <Td className="font-mono">{symbol}</Td>
                    <Td><Chip tone={RESULT_TONE[result] ?? "muted"}>{result}</Chip></Td>
                  </tr>
                );
              })}
            </tbody>
          </Table>
        )}
      </Card>
    </div>
  );
}
