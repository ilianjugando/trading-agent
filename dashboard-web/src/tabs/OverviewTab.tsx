import type { DashboardData } from "../api/client";
import type { DiscoveryEntry } from "../api/client";
import { PortfolioChart } from "../components/PortfolioChart";
import { Card, Chip, EmptyState, SectionLabel, StatTile, Table, Td, Th } from "../components/primitives";
import { BUCKET_LABELS, num, pct, pnlClass, POOL_LABELS, relativeTime, usd } from "../lib/format";

const DIAGNOSIS_STYLE: Record<string, { border: string; bg: string; text: string; label: string }> = {
  NO_OPPORTUNITIES_FOUND: { border: "border-warn", bg: "bg-warn-soft", text: "text-warn", label: "Sin oportunidades viables" },
  SYSTEM_FAILED_TO_DEPLOY: { border: "border-bad", bg: "bg-bad-soft", text: "text-bad", label: "Fallo del sistema al desplegar capital" },
};

/** P&L de hoy: primer vs. ultimo punto de la serie combinada que caiga en
 * el dia local actual. N/A honesto si todavia no hay ningun punto de hoy
 * -- no se aproxima con la serie completa, seria una cifra distinta a lo
 * que dice, "P&L de HOY". */
function todayPnl(series: DashboardData["portfolio_series"]): { usd: number; pct: number } | null {
  const startOfDay = new Date();
  startOfDay.setHours(0, 0, 0, 0);
  const todays = series.filter((p) => new Date(p.timestamp) >= startOfDay);
  if (todays.length < 2) return null;
  const first = todays[0].total_value;
  const last = todays[todays.length - 1].total_value;
  if (first <= 0) return null;
  return { usd: last - first, pct: ((last - first) / first) * 100 };
}

export function OverviewTab({ data }: { data: DashboardData }) {
  const { portfolio, portfolio_series, risk, discovery, live_positions } = data;
  const pnl = todayPnl(portfolio_series);
  const alerts = Object.entries(data.deployment_alerts);

  return (
    <div className="flex flex-col gap-6">
      {alerts.length > 0 && (
        <div className="flex flex-col gap-2">
          {alerts.map(([pool, alert]) => {
            const style = DIAGNOSIS_STYLE[alert.diagnosis ?? ""] ?? DIAGNOSIS_STYLE.NO_OPPORTUNITIES_FOUND;
            return (
              <div key={pool} className={`rounded-lg border ${style.border} ${style.bg} px-4 py-3`}>
                <div className={`font-mono text-xs font-bold uppercase tracking-wide ${style.text}`}>
                  {POOL_LABELS[pool] ?? pool} · {style.label}
                </div>
                <div className="mt-1 text-sm text-fg">{alert.detail}</div>
                <div className="mt-1 text-xs text-muted">
                  {alert.assets_scanned} escaneados · {alert.shortlisted} con esperanza positiva · {relativeTime(alert.age_minutes)}
                </div>
              </div>
            );
          })}
        </div>
      )}

      <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-5">
        <StatTile label="Valor del portafolio" value={usd(portfolio.total_capital)} sub="stocks + crypto" />
        <StatTile
          label="P&L de hoy"
          value={pnl ? usd(pnl.usd, { showSign: true }) : "N/A"}
          sub={pnl ? pct(pnl.pct, { showSign: true }) : "aún sin historial de hoy"}
          valueClassName={pnl ? pnlClass(pnl.usd) : "text-muted"}
        />
        <StatTile label="Cash" value={usd(portfolio.total_capital ? portfolio.total_capital - portfolio.deployed_capital : null)} sub={`${pct(portfolio.cash_pct)} del capital`} />
        <StatTile label="Invertido" value={usd(portfolio.deployed_capital)} sub={`${portfolio.n_positions} posiciones`} />
        <StatTile label="Exposición" value={pct(risk.exposure_pct)} sub={`tope ${pct(risk.max_deployed_pct)}`} />
      </div>

      <Card>
        <SectionLabel>Rendimiento del portafolio</SectionLabel>
        {portfolio_series.length < 2 ? (
          <EmptyState>
            El historial de rendimiento se está construyendo — volvé en unos días.
            <br />
            Se necesita más de una medición para dibujar la curva; por ahora hay {portfolio_series.length}.
          </EmptyState>
        ) : (
          <PortfolioChart series={portfolio_series} />
        )}
      </Card>

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        {(["stocks", "crypto"] as const).map((pool) => {
          const entry = discovery[pool] as DiscoveryEntry | null;
          const candidates = entry?.top_candidates ?? [];
          const held = new Set(live_positions.filter((p) => p.pool === pool).map((p) => p.symbol));
          return (
            <Card key={pool}>
              <SectionLabel>Top oportunidades ahora · {POOL_LABELS[pool]}</SectionLabel>
              {candidates.length === 0 ? (
                <EmptyState>Sin candidatos en el último escaneo.</EmptyState>
              ) : (
                <Table>
                  <thead>
                    <tr>
                      <Th>Símbolo</Th>
                      <Th align="right">Score</Th>
                      <Th align="right">EV%</Th>
                      <Th>Estado</Th>
                    </tr>
                  </thead>
                  <tbody>
                    {candidates.slice(0, 8).map((c) => (
                      <tr key={c.symbol}>
                        <Td className="font-mono">{c.symbol}</Td>
                        <Td align="right" className="font-mono text-ai">{num(c.score)}</Td>
                        <Td align="right" className={`font-mono ${pnlClass(c.ev_pct)}`}>{pct(c.ev_pct, { showSign: true })}</Td>
                        <Td>
                          {held.has(c.symbol) ? <Chip tone="ok">En posición</Chip> : <Chip>Observando</Chip>}
                        </Td>
                      </tr>
                    ))}
                  </tbody>
                </Table>
              )}
            </Card>
          );
        })}
      </div>

      <Card>
        <SectionLabel>Posiciones abiertas</SectionLabel>
        {live_positions.length === 0 ? (
          <EmptyState>No hay posiciones abiertas.</EmptyState>
        ) : (
          <Table>
            <thead>
              <tr>
                <Th>Símbolo</Th>
                <Th>Pool</Th>
                <Th>Bucket</Th>
                <Th align="right">Valor</Th>
                <Th align="right">P&L</Th>
              </tr>
            </thead>
            <tbody>
              {live_positions.slice(0, 8).map((p) => (
                <tr key={`${p.pool}-${p.symbol}`}>
                  <Td className="font-mono">{p.symbol}</Td>
                  <Td>{POOL_LABELS[p.pool] ?? p.pool}</Td>
                  <Td>{p.bucket ? BUCKET_LABELS[p.bucket] ?? p.bucket : "—"}</Td>
                  <Td align="right" className="font-mono">{usd(p.market_value)}</Td>
                  <Td align="right" className={`font-mono ${pnlClass(p.pnl_usd)}`}>
                    {p.pnl_usd !== null ? `${usd(p.pnl_usd, { showSign: true })} (${pct(p.pnl_pct, { showSign: true })})` : "N/A"}
                  </Td>
                </tr>
              ))}
            </tbody>
          </Table>
        )}
      </Card>

      {risk.alerts.length > 0 && (
        <Card>
          <SectionLabel>Alertas de riesgo</SectionLabel>
          <div className="flex flex-col gap-2">
            {risk.alerts.map((a, i) => (
              <div key={i} className="flex items-center gap-2 text-sm">
                <Chip tone={a.level === "warning" ? "warn" : "muted"}>{a.code}</Chip>
                <span className="text-muted">{a.detail}</span>
              </div>
            ))}
          </div>
        </Card>
      )}
    </div>
  );
}
