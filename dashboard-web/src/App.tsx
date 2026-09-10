import * as Tabs from "@radix-ui/react-tabs";
import { BotControl } from "./components/BotControl";
import { ThemeToggle } from "./components/ThemeToggle";
import { useDashboardData } from "./hooks/useDashboardData";
import { OverviewTab } from "./tabs/OverviewTab";
import { PositionsTab } from "./tabs/PositionsTab";
import { OpportunitiesTab } from "./tabs/OpportunitiesTab";
import { ActivityTab } from "./tabs/ActivityTab";
import { RiskTab } from "./tabs/RiskTab";
import { TradesTab } from "./tabs/TradesTab";
import { StrategiesTab } from "./tabs/StrategiesTab";

const TABS = [
  { value: "overview", label: "Overview" },
  { value: "positions", label: "Posiciones" },
  { value: "opportunities", label: "Oportunidades" },
  { value: "activity", label: "Actividad" },
  { value: "risk", label: "Riesgo" },
  { value: "trades", label: "Trades" },
  { value: "strategies", label: "Estrategias" },
] as const;

const TAB_TRIGGER_CLASS =
  "inline-flex cursor-pointer items-center whitespace-nowrap border-b-2 border-transparent px-4 py-2.5 font-sans text-[13px] font-semibold text-muted transition-colors hover:text-fg data-[state=active]:border-glow data-[state=active]:text-fg";

export default function App() {
  const { data, error } = useDashboardData();

  return (
    <div className="mx-auto max-w-[1400px] px-4 py-6 sm:px-6">
      <header className="mb-2 flex flex-wrap items-center justify-between gap-3 pb-3">
        <div className="flex items-center gap-2.5">
          <span className="relative h-2.5 w-2.5 flex-none">
            <span className="absolute inset-0 rounded-full bg-glow shadow-[0_0_6px_1px_var(--glow)]" />
          </span>
          <h1 className="font-sans text-lg font-bold tracking-tight text-fg">Trading Agent · Command Center</h1>
        </div>
        <div className="flex items-center gap-3">
          <BotControl />
          <ThemeToggle />
          <span className="font-mono text-xs text-muted">
            {data ? `actualizado ${new Date(data.generated_at).toLocaleTimeString("es-AR")}` : "—"}
          </span>
        </div>
      </header>

      {error && (
        <div className="mb-4 rounded-lg border border-bad bg-bad-soft px-4 py-3 text-sm text-bad">
          No se pudo conectar con la API ({error}). ¿Está corriendo <code className="font-mono">uvicorn api.app:app</code>?
        </div>
      )}

      <Tabs.Root defaultValue="overview">
        <Tabs.List className="mb-6 flex gap-1 overflow-x-auto border-b border-border">
          {TABS.map((t) => (
            <Tabs.Trigger key={t.value} value={t.value} className={TAB_TRIGGER_CLASS}>
              {t.label}
            </Tabs.Trigger>
          ))}
        </Tabs.List>

        {!data ? (
          <div className="py-16 text-center text-sm text-muted">Cargando datos del bot…</div>
        ) : (
          <>
            <Tabs.Content value="overview">
              <OverviewTab data={data} />
            </Tabs.Content>
            <Tabs.Content value="positions">
              <PositionsTab positions={data.live_positions} />
            </Tabs.Content>
            <Tabs.Content value="opportunities">
              <OpportunitiesTab discovery={data.discovery} livePositions={data.live_positions} />
            </Tabs.Content>
            <Tabs.Content value="activity">
              <ActivityTab data={data} />
            </Tabs.Content>
            <Tabs.Content value="risk">
              <RiskTab risk={data.risk} breakers={data.breakers} />
            </Tabs.Content>
            <Tabs.Content value="trades">
              <TradesTab trades={data.closed_trades} />
            </Tabs.Content>
            <Tabs.Content value="strategies">
              <StrategiesTab strategies={data.strategy_performance} />
            </Tabs.Content>
          </>
        )}
      </Tabs.Root>
    </div>
  );
}
