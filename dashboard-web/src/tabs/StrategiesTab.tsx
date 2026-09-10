import type { StrategyPerformance } from "../api/client";
import { Card, Chip, EmptyState, Table, Td, Th } from "../components/primitives";
import { pct } from "../lib/format";

export function StrategiesTab({ strategies }: { strategies: StrategyPerformance[] }) {
  return (
    <Card>
      {strategies.length === 0 ? (
        <EmptyState>El torneo todavía no registró propuestas. Corré el orchestrator primero.</EmptyState>
      ) : (
        <>
          <Table>
            <thead>
              <tr>
                <Th>Estrategia</Th>
                <Th>Estado</Th>
                <Th align="right">Cerradas</Th>
                <Th align="right">Abiertas</Th>
                <Th align="right">Aciertos</Th>
                <Th align="right">Retorno prom.</Th>
                <Th align="right">Mejor</Th>
                <Th align="right">Peor</Th>
              </tr>
            </thead>
            <tbody>
              {strategies.map((s) => (
                <tr key={s.strategy}>
                  <Td className="font-mono font-semibold">{s.strategy}</Td>
                  <Td>
                    <Chip tone={s.status === "scored" ? "ok" : "muted"}>
                      {s.status === "scored" ? "Con datos" : "Esperando resultados"}
                    </Chip>
                  </Td>
                  <Td align="right" className="font-mono">{s.scored}</Td>
                  <Td align="right" className="font-mono">{s.open_proposals}</Td>
                  <Td align="right" className="font-mono">{s.win_rate !== null ? pct(s.win_rate) : "—"}</Td>
                  <Td align="right" className="font-mono">{s.avg_return_pct !== null ? pct(s.avg_return_pct, { showSign: true }) : "—"}</Td>
                  <Td align="right" className="font-mono text-accent">{s.best_pct !== null ? pct(s.best_pct, { showSign: true }) : "—"}</Td>
                  <Td align="right" className="font-mono text-bad">{s.worst_pct !== null ? pct(s.worst_pct, { showSign: true }) : "—"}</Td>
                </tr>
              ))}
            </tbody>
          </Table>
          <div className="mt-3 text-xs text-muted">
            "Cerradas" es lo único que da confianza: una estrategia con pocos resultados tiene un porcentaje de aciertos, pero no uno en el que valga la pena creer.
          </div>
        </>
      )}
    </Card>
  );
}
