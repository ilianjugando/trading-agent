import type { ClosedTrade } from "../api/client";
import { Card, Chip, EmptyState, Table, Td, Th } from "../components/primitives";
import { BUCKET_LABELS, pnlClass, POOL_LABELS, usd } from "../lib/format";

export function TradesTab({ trades }: { trades: ClosedTrade[] }) {
  return (
    <Card>
      {trades.length === 0 ? (
        <EmptyState>
          Sin operaciones cerradas todavía.
          <br />
          Esta tabla se puebla sola apenas ocurra el primer cierre real (stop tocado) — no hace falta tocar nada.
        </EmptyState>
      ) : (
        <Table>
          <thead>
            <tr>
              <Th>Símbolo</Th>
              <Th>Pool</Th>
              <Th>Bucket</Th>
              <Th align="right">Salida</Th>
              <Th align="right">Stop</Th>
              <Th>Resultado</Th>
              <Th align="right">Tamaño</Th>
              <Th>Cierre</Th>
            </tr>
          </thead>
          <tbody>
            {trades.map((t, i) => (
              <tr key={i}>
                <Td className="font-mono">{t.symbol}</Td>
                <Td>{POOL_LABELS[t.pool] ?? t.pool}</Td>
                <Td>{t.bucket ? BUCKET_LABELS[t.bucket] ?? t.bucket : "—"}</Td>
                <Td align="right" className="font-mono">{t.exit_price !== null ? usd(t.exit_price) : "N/A"}</Td>
                <Td align="right" className="font-mono text-bad">{t.stop !== null ? usd(t.stop) : "N/A"}</Td>
                <Td>
                  {t.won === null ? (
                    <Chip>N/A</Chip>
                  ) : (
                    <Chip tone={t.won ? "ok" : "bad"}>{t.won ? "Ganó" : "Perdió"}</Chip>
                  )}
                </Td>
                <Td align="right" className={`font-mono ${pnlClass(t.won === null ? null : t.won ? 1 : -1)}`}>
                  {t.position_usd !== null ? usd(t.position_usd) : "N/A"}
                </Td>
                <Td className="font-mono text-xs">{new Date(t.closed_at).toLocaleString("es-AR")}</Td>
              </tr>
            ))}
          </tbody>
        </Table>
      )}
    </Card>
  );
}
