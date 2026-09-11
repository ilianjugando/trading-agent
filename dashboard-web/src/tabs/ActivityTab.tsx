import type { DashboardData, RawLog } from "../api/client";
import { Card, Chip, EmptyState, SectionLabel, Table, Td, Th } from "../components/primitives";
import { POOL_LABELS, relativeTime, usd } from "../lib/format";

/** Cada codigo de resultado en lenguaje humano. Sin esto el feed dice
 * el codigo crudo del log y hay que abrir el fuente para saber que paso. */
export const RESULT_LABELS: Record<string, string> = {
  executed: "Compra ejecutada",
  stopped_out: "Cerrada por stop-loss",
  skipped: "El panel dijo que no",
  skipped_panel_degraded: "El panel no pudo evaluar (modelos caidos)",
  skipped_already_held: "Ya la tenemos en cartera",
  skipped_portfolio_full: "Cartera llena",
  skipped_capital_reserve: "Reserva de capital alcanzada",
  skipped_sizing: "Quedaba muy chica para operar",
  skipped_already_running: "Otro ciclo ya estaba corriendo",
  blocked_by_kill_switch: "Bloqueada por el corte de emergencia",
  phantom_position_cleared: "Posicion fantasma corregida",
  position_reconciliation_alert: "El estado no coincide con el broker",
  equity_reconciliation_alert: "Valuacion propia distinta a la del exchange",
  market_data_errors: "Fallos bajando datos de mercado",
  custom_strategies_error: "Error leyendo estrategias propias",
  stale_lock_taken_over: "Se retomo un lock de un proceso muerto",
  order_rejected: "Orden rechazada por el broker",
  order_not_filled: "La orden no se lleno",
  exit_error: "Fallo al cerrar la posicion",
  rejected_by_spend_guard: "Freno el limite de gasto",
  capital_deployment_alert: "Alerta: no se desplego capital",
  halted: "Bot detenido por el circuit breaker",
  error: "Error del ciclo",
  scan: "Escaneo de mercado",
  tournament: "Torneo de estrategias",
  stop_trailed: "Stop subido",
  market_closed: "Mercado cerrado",
};

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
  skipped_panel_degraded: "bad",
  blocked_by_kill_switch: "warn",
  phantom_position_cleared: "warn",
  position_reconciliation_alert: "bad",
  equity_reconciliation_alert: "warn",
  market_data_errors: "warn",
  custom_strategies_error: "bad",
  exit_error: "bad",
  skipped_already_held: "muted",
  skipped_already_running: "muted",
};

/** Una ejecucion como frase, no como fila de log. Crypto y acciones
 * guardan campos distintos (instId/side/usd_amount vs symbol/action/qty),
 * asi que se leen los dos de a uno. */
function executionSentence(t: RawLog): string {
  const symbol = (t.instId ?? t.symbol ?? "?") as string;
  const side = String(t.side ?? t.action ?? "").toLowerCase();
  const verb = side.startsWith("s") ? "Vendió" : "Compró";
  const qty = t.qty as number | undefined;
  const price = t.price as number | undefined;
  const usdAmount = (t.usd_amount ?? (t.sizing as { usd?: number } | undefined)?.usd) as number | undefined;

  const what = qty ? `${qty.toLocaleString("es-AR")} ${symbol}` : symbol;
  const at = price ? ` a ${usd(price)}` : "";
  const total = usdAmount ? ` · ${usd(usdAmount)}` : "";
  const why = t.reason === "stop_loss" ? " · por stop-loss" : "";
  return `${verb} ${what}${at}${total}${why}`;
}

export function ActivityTab({ data }: { data: DashboardData }) {
  const { pools, watchlist, panel_sentiment, recent_decisions, recent_trades } = data;

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
        <SectionLabel>Ejecuciones</SectionLabel>
        {recent_trades.length === 0 ? (
          <EmptyState>El bot todavía no ejecutó ninguna orden.</EmptyState>
        ) : (
          <div className="mb-6 flex flex-col gap-2">
            {recent_trades.slice(0, 12).map((t, i) => (
              <div key={i} className="flex flex-wrap items-center justify-between gap-2 rounded-md border border-border bg-card-2 px-3 py-2">
                <div className="flex flex-col">
                  <span className="text-sm text-fg">{executionSentence(t)}</span>
                  <span className="font-mono text-[11px] text-muted">
                    {POOL_LABELS[String(t.pool)] ?? String(t.pool)}
                    {(t.sizing as { bucket?: string } | undefined)?.bucket ? ` · ${(t.sizing as { bucket?: string }).bucket}` : ""}
                  </span>
                </div>
                <span className="font-mono text-xs text-muted">
                  {t.timestamp ? new Date(String(t.timestamp)).toLocaleString("es-AR") : ""}
                </span>
              </div>
            ))}
          </div>
        )}

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
                    <Td><Chip tone={RESULT_TONE[result] ?? "muted"}>{RESULT_LABELS[result] ?? result}</Chip></Td>
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
