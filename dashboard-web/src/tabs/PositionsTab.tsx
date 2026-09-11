import { useState } from "react";
import type { LivePosition } from "../api/client";
import { Card, EmptyState, Table, Td, Th } from "../components/primitives";
import { Sparkline } from "../components/Sparkline";
import { BUCKET_LABELS, num, pct, pnlClass, POOL_LABELS, relativeTime, usd } from "../lib/format";

function Row({ p, spark }: { p: LivePosition; spark?: number[] }) {
  const [open, setOpen] = useState(false);
  const hasDetail = p.reasoning || p.asymmetry;

  return (
    <>
      <tr onClick={() => hasDetail && setOpen((v) => !v)} className={hasDetail ? "cursor-pointer" : ""}>
        <Td className="font-mono font-semibold">{p.symbol}</Td>
        <Td><Sparkline points={spark ?? []} /></Td>
        <Td>{POOL_LABELS[p.pool] ?? p.pool}</Td>
        <Td>{p.bucket ? BUCKET_LABELS[p.bucket] ?? p.bucket : "—"}</Td>
        <Td align="right" className="font-mono">{usd(p.entry_price)}</Td>
        <Td align="right" className="font-mono">{p.current_price !== null ? usd(p.current_price) : "N/A"}</Td>
        <Td align="right" className="font-mono">{usd(p.market_value)}</Td>
        <Td align="right" className={`font-mono ${pnlClass(p.pnl_usd)}`}>
          {p.pnl_usd !== null ? `${usd(p.pnl_usd, { showSign: true })} (${pct(p.pnl_pct, { showSign: true })})` : "N/A"}
        </Td>
        <Td align="right" className="font-mono text-bad">{usd(p.stop)}</Td>
        <Td align="right" className="font-mono text-ai">{p.confidence !== null ? pct(p.confidence * 100) : "N/A"}</Td>
        <Td>{p.opened_at ? relativeTime((Date.now() - new Date(p.opened_at).getTime()) / 60000) : "N/A"}</Td>
      </tr>
      {open && hasDetail && (
        <tr>
          <Td colSpan={11} className="bg-card-2">
            <div className="flex flex-col gap-2 py-2 text-sm">
              {p.strategy && <div><span className="text-muted">Estrategias: </span>{p.strategy}</div>}
              {p.reasoning && <div><span className="text-muted">Razón del panel: </span>{p.reasoning}</div>}
              {p.opportunity_score !== null && <div><span className="text-muted">Opportunity score: </span><span className="font-mono text-ai">{num(p.opportunity_score)}</span></div>}
              {p.asymmetry && (
                <div className="grid grid-cols-2 gap-x-6 gap-y-1 sm:grid-cols-4">
                  <div><span className="text-muted">Objetivo: </span><span className="font-mono text-accent">+{pct(p.asymmetry.target_pct)}</span></div>
                  <div><span className="text-muted">Stop: </span><span className="font-mono text-bad">-{pct(p.asymmetry.stop_pct)}</span></div>
                  <div><span className="text-muted">R:R: </span><span className="font-mono">{num(p.asymmetry.reward_risk, 2)}</span></div>
                  <div><span className="text-muted">Win prob: </span><span className="font-mono">{pct(p.asymmetry.win_prob * 100)}</span></div>
                  <div><span className="text-muted">EV: </span><span className={`font-mono ${pnlClass(p.asymmetry.expected_value_pct)}`}>{pct(p.asymmetry.expected_value_pct, { showSign: true })}</span></div>
                  <div><span className="text-muted">Muestra: </span><span className="font-mono">{p.asymmetry.sample_size}</span></div>
                </div>
              )}
            </div>
          </Td>
        </tr>
      )}
    </>
  );
}

export function PositionsTab({ positions, sparklines = {} }: { positions: LivePosition[]; sparklines?: Record<string, number[]> }) {
  return (
    <Card>
      {positions.length === 0 ? (
        <EmptyState>No hay posiciones abiertas.</EmptyState>
      ) : (
        <Table>
          <thead>
            <tr>
              <Th>Símbolo</Th>
              <Th>Precio</Th>
              <Th>Pool</Th>
              <Th>Bucket</Th>
              <Th align="right">Entrada</Th>
              <Th align="right">Actual</Th>
              <Th align="right">Valor</Th>
              <Th align="right">P&L</Th>
              <Th align="right">Stop</Th>
              <Th align="right">Confianza</Th>
              <Th>Tiempo</Th>
            </tr>
          </thead>
          <tbody>
            {positions.map((p) => (
              <Row key={`${p.pool}-${p.symbol}`} p={p} spark={sparklines[p.symbol]} />
            ))}
          </tbody>
        </Table>
      )}
      {positions.some((p) => p.reasoning || p.asymmetry) && (
        <div className="mt-3 text-xs text-muted">Clic en una fila para ver la tesis de entrada.</div>
      )}
    </Card>
  );
}
