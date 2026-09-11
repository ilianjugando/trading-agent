import { useEffect, useState } from "react";
import type { CustomStrategy, StrategyRule } from "../api/client";
import { api } from "../api/client";
import { Card, Chip, EmptyState, SectionLabel } from "../components/primitives";

/** Etiquetas legibles para los indicadores. El selector no puede mostrar
 * `change_5_pct` y esperar que alguien sepa qué significa. */
const INDICATOR_LABELS: Record<string, string> = {
  rsi: "RSI (14)",
  sma_trend: "Tendencia (SMA rápida vs lenta)",
  change_pct: "Cambio en la última barra %",
  change_5_pct: "Cambio en 5 barras %",
  above_high_20: "Rompe el máximo de 20 barras",
  below_low_20: "Rompe el mínimo de 20 barras",
};

const BOOLEAN_INDICATORS = ["above_high_20", "below_low_20"];
const TREND_VALUES = ["up", "down", "flat"];

function emptyRule(): StrategyRule {
  return { indicator: "rsi", op: "<", value: 40 };
}

export function StrategyBuilder({ onChanged }: { onChanged?: () => void }) {
  const [list, setList] = useState<CustomStrategy[]>([]);
  const [indicators, setIndicators] = useState<string[]>([]);
  const [operators, setOperators] = useState<string[]>([]);
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [rules, setRules] = useState<StrategyRule[]>([emptyRule()]);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [idea, setIdea] = useState("");
  const [translating, setTranslating] = useState(false);

  async function translate() {
    setTranslating(true);
    setError(null);
    try {
      const t = await api.translateStrategy(idea);
      setRules(t.entry);
      if (!description) setDescription(t.resumen);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setTranslating(false);
    }
  }

  async function refresh() {
    const r = await api.customStrategies();
    setList(r.strategies);
    setIndicators(r.available_indicators);
    setOperators(r.available_operators);
  }

  useEffect(() => {
    refresh().catch((e) => setError(String(e)));
  }, []);

  async function save() {
    setBusy(true);
    setError(null);
    try {
      const r = await api.saveStrategy({ name, description, entry: rules });
      setList(r.strategies);
      setName("");
      setDescription("");
      setRules([emptyRule()]);
      onChanged?.();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  async function remove(n: string) {
    setList((await api.deleteStrategy(n)).strategies);
    onChanged?.();
  }

  function setRule(i: number, patch: Partial<StrategyRule>) {
    setRules(rules.map((r, j) => (j === i ? { ...r, ...patch } : r)));
  }

  return (
    <div className="flex flex-col gap-4">
      <Card>
        <SectionLabel>Mis estrategias</SectionLabel>
        {list.length === 0 ? (
          <EmptyState>Todavía no creaste ninguna. Las de abajo se suman a las que ya trae el bot.</EmptyState>
        ) : (
          <div className="flex flex-col gap-2">
            {list.map((s) => (
              <div key={s.name} className="flex flex-wrap items-center justify-between gap-2 rounded-md border border-border bg-card-2 px-3 py-2">
                <div className="flex flex-col">
                  <span className="font-mono text-sm font-semibold text-fg">{s.name}</span>
                  {s.description && <span className="text-xs text-muted">{s.description}</span>}
                  <span className="mt-1 font-mono text-[11px] text-muted">
                    {s.entry.map((r) => `${INDICATOR_LABELS[r.indicator] ?? r.indicator} ${r.op} ${r.value}`).join("  ·  ")}
                  </span>
                </div>
                <div className="flex items-center gap-2">
                  <Chip tone="ok">operando</Chip>
                  <button onClick={() => remove(s.name)} className="cursor-pointer font-mono text-xs text-bad hover:underline">
                    borrar
                  </button>
                </div>
              </div>
            ))}
          </div>
        )}
      </Card>

      <Card>
        <SectionLabel>Crear una estrategia</SectionLabel>

        <div className="mb-5 rounded-lg border border-ai bg-ai-soft p-3">
          <span className="text-[11px] uppercase tracking-wider text-ai">Describila en castellano</span>
          <div className="mt-2 flex flex-wrap gap-2">
            <input
              value={idea}
              onChange={(e) => setIdea(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && idea && translate()}
              placeholder="comprar cuando el RSI baje de 35 y la tendencia siga alcista"
              className="min-w-64 flex-1 rounded-md border border-border bg-card px-2 py-1.5 text-sm text-fg"
            />
            <button
              onClick={translate}
              disabled={translating || !idea}
              className="cursor-pointer rounded-md bg-ai px-4 py-2 font-mono text-sm font-semibold text-bg disabled:opacity-50"
            >
              {translating ? "Traduciendo…" : "Traducir a reglas"}
            </button>
          </div>
          <p className="mt-2 text-xs text-muted">
            El modelo propone las condiciones y las completa abajo. Revisalas antes de guardar: lo que devuelve es una
            propuesta, no algo que se active solo.
          </p>
        </div>
        <div className="flex flex-wrap gap-3">
          <label className="flex flex-col gap-1">
            <span className="text-[11px] uppercase tracking-wider text-muted">Nombre</span>
            <input
              value={name}
              onChange={(e) => setName(e.target.value.replace(/\s+/g, "_").toLowerCase())}
              placeholder="mi_estrategia"
              className="w-52 rounded-md border border-border bg-card-2 px-2 py-1.5 font-mono text-sm text-fg"
            />
          </label>
          <label className="flex flex-1 flex-col gap-1">
            <span className="text-[11px] uppercase tracking-wider text-muted">Qué hace (para acordarte después)</span>
            <input
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              placeholder="Compra retrocesos mientras la tendencia sigue alcista"
              className="w-full rounded-md border border-border bg-card-2 px-2 py-1.5 text-sm text-fg"
            />
          </label>
        </div>

        <div className="mt-4 flex flex-col gap-2">
          <span className="text-[11px] uppercase tracking-wider text-muted">Compra cuando TODO esto se cumpla</span>
          {rules.map((r, i) => (
            <div key={i} className="flex flex-wrap items-center gap-2">
              <select
                value={r.indicator}
                onChange={(e) => setRule(i, { indicator: e.target.value, value: BOOLEAN_INDICATORS.includes(e.target.value) ? true : e.target.value === "sma_trend" ? "up" : 0 })}
                className="rounded-md border border-border bg-card-2 px-2 py-1.5 text-sm text-fg"
              >
                {indicators.map((ind) => (
                  <option key={ind} value={ind}>{INDICATOR_LABELS[ind] ?? ind}</option>
                ))}
              </select>
              <select
                value={r.op}
                onChange={(e) => setRule(i, { op: e.target.value })}
                className="rounded-md border border-border bg-card-2 px-2 py-1.5 font-mono text-sm text-fg"
              >
                {operators.map((o) => (
                  <option key={o} value={o}>{o}</option>
                ))}
              </select>
              {r.indicator === "sma_trend" ? (
                <select
                  value={String(r.value)}
                  onChange={(e) => setRule(i, { value: e.target.value })}
                  className="rounded-md border border-border bg-card-2 px-2 py-1.5 font-mono text-sm text-fg"
                >
                  {TREND_VALUES.map((v) => (
                    <option key={v} value={v}>{v}</option>
                  ))}
                </select>
              ) : BOOLEAN_INDICATORS.includes(r.indicator) ? (
                <select
                  value={String(r.value)}
                  onChange={(e) => setRule(i, { value: e.target.value === "true" })}
                  className="rounded-md border border-border bg-card-2 px-2 py-1.5 font-mono text-sm text-fg"
                >
                  <option value="true">sí</option>
                  <option value="false">no</option>
                </select>
              ) : (
                <input
                  type="number"
                  value={Number(r.value)}
                  onChange={(e) => setRule(i, { value: Number(e.target.value) })}
                  className="w-28 rounded-md border border-border bg-card-2 px-2 py-1.5 font-mono text-sm text-fg"
                />
              )}
              {rules.length > 1 && (
                <button onClick={() => setRules(rules.filter((_, j) => j !== i))} className="cursor-pointer font-mono text-xs text-muted hover:text-bad">
                  quitar
                </button>
              )}
            </div>
          ))}
          <button onClick={() => setRules([...rules, emptyRule()])} className="self-start cursor-pointer font-mono text-xs text-accent hover:underline">
            + agregar condición
          </button>
        </div>

        {error && <div className="mt-3 rounded-lg border border-bad bg-bad-soft px-3 py-2 text-sm text-bad">{error}</div>}

        <button
          onClick={save}
          disabled={busy || !name}
          className="mt-4 cursor-pointer rounded-md bg-glow px-4 py-2 font-mono text-sm font-semibold text-bg disabled:opacity-50"
        >
          {busy ? "Guardando…" : "Guardar y activar"}
        </button>
        <p className="mt-3 text-xs text-muted">
          La salida no se define acá: el stop y el objetivo salen de la medición de asimetría del activo, igual que en el
          resto del sistema. Una estrategia guardada entra al escaneo en vivo y se puede backtestear arriba.
        </p>
      </Card>
    </div>
  );
}
