import type { DexMover, Mover } from "../api/client";
import { Card, Chip, EmptyState, SectionLabel, Table, Td, Th } from "../components/primitives";
import { useMarketRadar } from "../hooks/useMarketRadar";
import { pnlClass, usd } from "../lib/format";

/** usd() redondea a 0 decimales -- perfecto para valores de posicion,
 * inutil para el precio de un token que vale fracciones de centavo (una
 * fila mostraba "US$ 0" para algo que valia $0,0002). Precio de mercado
 * necesita su propia escala de precision. */
function price(value: number): string {
  return `$${value.toLocaleString("es-AR", { minimumFractionDigits: 2, maximumFractionDigits: 6 })}`;
}

function ChangeCell({ pct, suspicious }: { pct: number; suspicious: boolean }) {
  return (
    <Td align="right" className={`font-mono ${pnlClass(pct)}`}>
      +{pct.toLocaleString("es-AR", { maximumFractionDigits: 1 })}%
      {suspicious && (
        <span className="ml-1.5">
          <Chip tone="warn">par nuevo?</Chip>
        </span>
      )}
    </Td>
  );
}

function CexTable({ movers }: { movers: Mover[] }) {
  if (movers.length === 0) return <EmptyState>Sin movimientos fuertes en este momento.</EmptyState>;
  return (
    <Table>
      <thead>
        <tr>
          <Th>Símbolo</Th>
          <Th>Exchange</Th>
          <Th align="right">Precio</Th>
          <Th align="right">Cambio 24h</Th>
          <Th align="right">Volumen 24h</Th>
        </tr>
      </thead>
      <tbody>
        {movers.map((m) => (
          <tr key={m.symbol}>
            <Td className="font-mono">{m.symbol}</Td>
            <Td>{m.exchange}</Td>
            <Td align="right" className="font-mono">{price(m.price)}</Td>
            <ChangeCell pct={m.change_24h_pct} suspicious={m.suspicious} />
            <Td align="right" className="font-mono">{usd(m.volume_24h_usd)}</Td>
          </tr>
        ))}
      </tbody>
    </Table>
  );
}

function DexTable({ movers }: { movers: DexMover[] }) {
  if (movers.length === 0) return <EmptyState>Sin movimientos fuertes en este momento.</EmptyState>;
  return (
    <Table>
      <thead>
        <tr>
          <Th>Par</Th>
          <Th>Blockchain</Th>
          <Th align="right">Precio</Th>
          <Th align="right">Cambio 24h</Th>
          <Th align="right">Volumen 24h</Th>
        </tr>
      </thead>
      <tbody>
        {movers.map((m) => (
          <tr key={m.symbol}>
            <Td className="font-mono text-xs">{m.symbol}</Td>
            <Td>{m.blockchain}</Td>
            <Td align="right" className="font-mono">{price(m.price)}</Td>
            <ChangeCell pct={m.change_24h_pct} suspicious={m.suspicious} />
            <Td align="right" className="font-mono">{usd(m.volume_24h_usd)}</Td>
          </tr>
        ))}
      </tbody>
    </Table>
  );
}

export function RadarTab() {
  const { radar, error } = useMarketRadar();

  return (
    <div className="flex flex-col gap-4">
      <div className="rounded-lg border border-ai bg-ai-soft px-4 py-3 text-sm text-ai">
        <b>Solo información — esto no ejecuta ni sugiere operaciones.</b> Muestra qué se está
        moviendo fuerte en exchanges y pares que OKX no lista (vía el screener de TradingView).
        Nada de esta pestaña llega al panel de decisión ni puede disparar una compra. Los pares
        de DEX en particular no tienen ningún filtro anti-estafa — tratar como pura observación.
      </div>

      {error && (
        <div className="rounded-lg border border-bad bg-bad-soft px-4 py-3 text-sm text-bad">
          No se pudo cargar el radar de mercado ({error}).
        </div>
      )}

      {!radar ? (
        <div className="py-16 text-center text-sm text-muted">Cargando radar de mercado…</div>
      ) : (
        <>
          <Card>
            <SectionLabel>Movimientos fuertes — otros exchanges (no solo OKX)</SectionLabel>
            <CexTable movers={radar.cex_movers} />
          </Card>
          <Card>
            <SectionLabel>Movimientos fuertes — DEX (donde nacen los meme coins)</SectionLabel>
            <DexTable movers={radar.dex_movers} />
          </Card>
        </>
      )}
    </div>
  );
}
