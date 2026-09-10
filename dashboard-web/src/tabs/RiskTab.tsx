import type { DashboardData } from "../api/client";
import { Card, Chip, EmptyState, SectionLabel, StatTile, Table, Td, Th } from "../components/primitives";
import { BUCKET_LABELS, pct, POOL_LABELS, usd } from "../lib/format";

export function RiskTab({
  risk,
  breakers,
}: {
  risk: DashboardData["risk"];
  breakers: DashboardData["breakers"];
}) {
  return (
    <div className="flex flex-col gap-4">
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-5">
        <StatTile label="Exposición total" value={pct(risk.exposure_pct)} />
        <StatTile label="Riesgo abierto (peor caso)" value={usd(risk.open_risk_usd)} sub="si TODOS los stops se tocan a la vez" />
        <StatTile label="Posiciones" value={`${risk.n_positions} / ${risk.max_open_positions}`} sub="abiertas / tope" />
        <StatTile label="Capital desplegado, tope" value={pct(risk.max_deployed_pct)} />
        <StatTile
          label="Mayor posición"
          value={risk.largest_position ? risk.largest_position.symbol : "—"}
          sub={risk.largest_position ? usd(risk.largest_position.market_value) : undefined}
        />
      </div>

      {risk.alerts.length > 0 && (
        <Card>
          <SectionLabel>Alertas</SectionLabel>
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

      <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
        <Card>
          <SectionLabel>Exposición por pool</SectionLabel>
          <Table>
            <tbody>
              {Object.entries(risk.exposure_by_pool).map(([pool, value]) => (
                <tr key={pool}>
                  <Td>{POOL_LABELS[pool] ?? pool}</Td>
                  <Td align="right" className="font-mono">{usd(value)}</Td>
                </tr>
              ))}
            </tbody>
          </Table>
        </Card>
        <Card>
          <SectionLabel>Exposición por bucket</SectionLabel>
          {Object.keys(risk.bucket_exposure).length === 0 ? (
            <EmptyState>Sin posiciones clasificadas.</EmptyState>
          ) : (
            <Table>
              <tbody>
                {Object.entries(risk.bucket_exposure).map(([bucket, value]) => (
                  <tr key={bucket}>
                    <Td>{BUCKET_LABELS[bucket] ?? bucket}</Td>
                    <Td align="right" className="font-mono">{usd(value)}</Td>
                  </tr>
                ))}
              </tbody>
            </Table>
          )}
        </Card>
      </div>

      <Card>
        <SectionLabel>Circuit breakers</SectionLabel>
        <Table>
          <thead>
            <tr>
              <Th>Pool</Th>
              <Th>Estado</Th>
              <Th align="right">Pérdidas consecutivas</Th>
              <Th align="right">Valor al abrir el día</Th>
            </tr>
          </thead>
          <tbody>
            {Object.entries(breakers).map(([pool, b]) => (
              <tr key={pool}>
                <Td>{POOL_LABELS[pool] ?? pool}</Td>
                <Td>
                  <Chip tone={b.halted ? "bad" : "ok"}>{b.halted ? "Detenido" : "Operando"}</Chip>
                  {b.halt_reason && <span className="ml-2 text-xs text-muted">{b.halt_reason}</span>}
                </Td>
                <Td align="right" className="font-mono">{b.consecutive_losses}</Td>
                <Td align="right" className="font-mono">{usd(b.day_start_value)}</Td>
              </tr>
            ))}
          </tbody>
        </Table>
      </Card>
    </div>
  );
}
