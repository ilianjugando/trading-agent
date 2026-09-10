import type { DiscoveryEntry, LivePosition } from "../api/client";
import { Card, Chip, EmptyState, SectionLabel, Table, Td, Th } from "../components/primitives";
import { num, pct, pnlClass, POOL_LABELS } from "../lib/format";

function FunnelBar({ scanned, shortlisted, rejected }: { scanned: number; shortlisted: number; rejected: number }) {
  const shortPct = scanned ? (shortlisted / scanned) * 100 : 0;
  return (
    <div className="flex flex-col gap-2">
      <div className="flex justify-between font-mono text-xs text-muted">
        <span>{scanned} escaneados</span>
        <span className="text-accent">{shortlisted} con esperanza positiva</span>
        <span>{rejected} rechazados</span>
      </div>
      <div className="h-2 w-full overflow-hidden rounded-full bg-card-2">
        <div className="h-full bg-accent" style={{ width: `${shortPct}%` }} />
      </div>
    </div>
  );
}

function PoolSection({ pool, entry, held }: { pool: string; entry: DiscoveryEntry | null; held: Set<string> }) {
  if (!entry) {
    return (
      <Card>
        <SectionLabel>{POOL_LABELS[pool] ?? pool}</SectionLabel>
        <EmptyState>Sin escaneo registrado todavía.</EmptyState>
      </Card>
    );
  }
  const reasons = Object.entries(entry.rejection_reasons ?? {}).sort((a, b) => b[1] - a[1]);
  const candidates = entry.top_candidates ?? [];

  return (
    <Card>
      <SectionLabel>{POOL_LABELS[pool] ?? pool}</SectionLabel>
      <FunnelBar
        scanned={entry.assets_scanned ?? 0}
        shortlisted={entry.assets_shortlisted ?? 0}
        rejected={entry.assets_rejected ?? 0}
      />

      {reasons.length > 0 && (
        <div className="mt-4 flex flex-wrap gap-2">
          {reasons.map(([reason, count]) => (
            <Chip key={reason} tone="muted">
              {reason} · {count}
            </Chip>
          ))}
        </div>
      )}

      <div className="mt-4">
        {candidates.length === 0 ? (
          <EmptyState>Sin candidatos con esperanza positiva en el último escaneo.</EmptyState>
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
              {candidates.map((c) => (
                <tr key={c.symbol}>
                  <Td className="font-mono">{c.symbol}</Td>
                  <Td align="right" className="font-mono text-ai">{num(c.score)}</Td>
                  <Td align="right" className={`font-mono ${pnlClass(c.ev_pct)}`}>{pct(c.ev_pct, { showSign: true })}</Td>
                  <Td>{held.has(c.symbol) ? <Chip tone="ok">En posición</Chip> : <Chip>Observando</Chip>}</Td>
                </tr>
              ))}
            </tbody>
          </Table>
        )}
      </div>
    </Card>
  );
}

export function OpportunitiesTab({
  discovery,
  livePositions,
}: {
  discovery: Record<string, DiscoveryEntry | null>;
  livePositions: LivePosition[];
}) {
  return (
    <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">
      {(["stocks", "crypto"] as const).map((pool) => (
        <PoolSection
          key={pool}
          pool={pool}
          entry={discovery[pool] as DiscoveryEntry | null}
          held={new Set(livePositions.filter((p) => p.pool === pool).map((p) => p.symbol))}
        />
      ))}
    </div>
  );
}
