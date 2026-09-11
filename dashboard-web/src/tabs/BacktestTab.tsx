import { AreaSeries, CandlestickSeries, createChart, createSeriesMarkers, type IChartApi, type Time } from "lightweight-charts";
import { useEffect, useRef, useState } from "react";
import type { BacktestRun } from "../api/client";
import { api } from "../api/client";
import { Card, EmptyState, SectionLabel, StatTile, Table, Td, Th } from "../components/primitives";
import { num, pct, pnlClass } from "../lib/format";
import { StrategyBuilder } from "./StrategyBuilder";

const PERIODS = ["6mo", "1y", "2y", "5y"];

/** Debajo de esto, Sharpe y % de aciertos son ruido estadistico. Mismo
 * criterio que _MIN_POINTS_FOR_STATS en api/data.py: un numero que aparenta
 * precision inexistente es peor que no mostrarlo. */
const MIN_TRADES_FOR_CONFIDENCE = 20;

function BacktestChart({ run }: { run: BacktestRun }) {
  const containerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;

    const styles = getComputedStyle(document.documentElement);
    const muted = styles.getPropertyValue("--muted").trim();
    const border = styles.getPropertyValue("--border").trim();
    const fg = styles.getPropertyValue("--fg").trim();

    const chart: IChartApi = createChart(el, {
      width: el.clientWidth,
      height: 360,
      layout: { background: { color: "transparent" }, textColor: muted, fontFamily: "Fira Code, monospace", fontSize: 11 },
      grid: { horzLines: { color: border }, vertLines: { visible: false } },
      rightPriceScale: { borderColor: border },
      timeScale: { borderColor: border, timeVisible: false },
      crosshair: { vertLine: { color: muted, labelBackgroundColor: fg }, horzLine: { color: muted, labelBackgroundColor: fg } },
    });

    const candles = chart.addSeries(CandlestickSeries, {
      upColor: "#26a69a",
      downColor: "#ef5350",
      borderVisible: false,
      wickUpColor: "#26a69a",
      wickDownColor: "#ef5350",
    });
    candles.setData(run.bars.map((b) => ({ time: b.time as Time, open: b.open, high: b.high, low: b.low, close: b.close })));

    // Cada operacion marcada sobre el precio: entrada abajo, salida arriba
    // y coloreada por resultado. Es la diferencia entre "la curva subio" y
    // poder ver DONDE entro y por que salio.
    const markers = run.trades
      .flatMap((t) => {
        const out = [];
        if (t.entry_time)
          out.push({ time: t.entry_time as Time, position: "belowBar" as const, color: "#2962ff", shape: "arrowUp" as const, text: "C" });
        if (t.exit_time)
          out.push({
            time: t.exit_time as Time,
            position: "aboveBar" as const,
            color: (t.pnl_pct ?? 0) >= 0 ? "#26a69a" : "#ef5350",
            shape: "arrowDown" as const,
            text: `${t.pnl_pct !== null ? (t.pnl_pct > 0 ? "+" : "") + t.pnl_pct + "%" : ""}`,
          });
        return out;
      })
      .sort((a, b) => (a.time as number) - (b.time as number));
    createSeriesMarkers(candles, markers);

    chart.timeScale().fitContent();
    const resize = () => chart.applyOptions({ width: el.clientWidth });
    window.addEventListener("resize", resize);
    return () => {
      window.removeEventListener("resize", resize);
      chart.remove();
    };
  }, [run]);

  return <div ref={containerRef} className="w-full" />;
}

function EquityCurve({ curve }: { curve: number[] }) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const el = ref.current;
    if (!el || curve.length < 2) return;
    const st = getComputedStyle(document.documentElement);
    const chart = createChart(el, {
      width: el.clientWidth,
      height: 200,
      layout: { background: { color: "transparent" }, textColor: st.getPropertyValue("--muted").trim(), fontFamily: "Fira Code, monospace", fontSize: 11 },
      grid: { horzLines: { color: st.getPropertyValue("--border").trim() }, vertLines: { visible: false } },
      rightPriceScale: { borderColor: st.getPropertyValue("--border").trim() },
      timeScale: { visible: false },
    });
    const up = curve[curve.length - 1] >= curve[0];
    const color = up ? "#26a69a" : "#ef5350";
    const s = chart.addSeries(AreaSeries, {
      lineColor: color,
      topColor: `color-mix(in srgb, ${color} 30%, transparent)`,
      bottomColor: "transparent",
      lineWidth: 2,
      priceFormat: { type: "custom", formatter: (v: number) => `${(v - 100).toFixed(1)}%` },
    });
    // El eje X es el numero de OPERACION, no el tiempo: la curva avanza
    // cuando se cierra un trade, no cuando pasa un dia.
    s.setData(curve.map((v, i) => ({ time: (i + 1) as Time, value: v })));
    chart.timeScale().fitContent();
    const resize = () => chart.applyOptions({ width: el.clientWidth });
    window.addEventListener("resize", resize);
    return () => { window.removeEventListener("resize", resize); chart.remove(); };
  }, [curve]);
  return <div ref={ref} className="w-full" />;
}

export function BacktestTab() {
  const [symbol, setSymbol] = useState("SPY");
  const [strategy, setStrategy] = useState("trend_follow");
  const [period, setPeriod] = useState("2y");
  const [run, setRun] = useState<BacktestRun | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [builderKey, setBuilderKey] = useState(0);

  const [strategies, setStrategies] = useState<string[]>(["breakout", "mean_reversion", "momentum", "trend_follow"]);

  useEffect(() => {
    api.customStrategies()
      .then((r) => setStrategies([...new Set([...["breakout", "mean_reversion", "momentum", "trend_follow"], ...r.strategies.map((s) => s.name)])].sort()))
      .catch(() => {});
  }, [builderKey]);

  async function go() {
    setLoading(true);
    setError(null);
    try {
      setRun(await api.backtest(symbol, strategy, period));
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      setRun(null);
    } finally {
      setLoading(false);
    }
  }

  const m = run?.metrics;
  const thin = (m?.closed_trades ?? 0) < MIN_TRADES_FOR_CONFIDENCE;

  return (
    <div className="flex flex-col gap-4">
      <Card>
        <div className="flex flex-wrap items-end gap-3">
          <label className="flex flex-col gap-1">
            <span className="text-[11px] uppercase tracking-wider text-muted">Símbolo</span>
            <input
              value={symbol}
              onChange={(e) => setSymbol(e.target.value.toUpperCase())}
              onKeyDown={(e) => e.key === "Enter" && go()}
              className="w-28 rounded-md border border-border bg-card-2 px-2 py-1.5 font-mono text-sm text-fg"
            />
          </label>
          <label className="flex flex-col gap-1">
            <span className="text-[11px] uppercase tracking-wider text-muted">Estrategia</span>
            <select
              value={strategy}
              onChange={(e) => setStrategy(e.target.value)}
              className="rounded-md border border-border bg-card-2 px-2 py-1.5 font-mono text-sm text-fg"
            >
              {strategies.map((s) => (
                <option key={s} value={s}>{s}</option>
              ))}
            </select>
          </label>
          <label className="flex flex-col gap-1">
            <span className="text-[11px] uppercase tracking-wider text-muted">Período</span>
            <select
              value={period}
              onChange={(e) => setPeriod(e.target.value)}
              className="rounded-md border border-border bg-card-2 px-2 py-1.5 font-mono text-sm text-fg"
            >
              {PERIODS.map((p) => (
                <option key={p} value={p}>{p}</option>
              ))}
            </select>
          </label>
          <button
            onClick={go}
            disabled={loading}
            className="cursor-pointer rounded-md bg-glow px-4 py-2 font-mono text-sm font-semibold text-bg disabled:opacity-50"
          >
            {loading ? "Corriendo…" : "Correr backtest"}
          </button>
        </div>
        <p className="mt-3 text-xs text-muted">
          Replay barra por barra sobre historia real, sin LLM y sin órdenes. El stop y el objetivo salen de la misma
          medición de asimetría que usa el bot en vivo, así que esto mide la estrategia que realmente opera.
        </p>
      </Card>

      {error && (
        <div className="rounded-lg border border-bad bg-bad-soft px-4 py-3 text-sm text-bad">{error}</div>
      )}

      {!run ? (
        <Card>
          <EmptyState>Elegí un símbolo y una estrategia, y corré el backtest.</EmptyState>
        </Card>
      ) : (
        <>
          <div className="grid grid-cols-2 gap-3 md:grid-cols-4 xl:grid-cols-7">
            <StatTile label="Operaciones" value={num(m!.closed_trades, 0)} sub={thin ? "muestra chica" : "muestra usable"} />
            <StatTile label="Retorno" value={pct(m!.total_return_pct, { showSign: true })} valueClassName={pnlClass(m!.total_return_pct)} />
            <StatTile label="Drawdown máx." value={pct(-m!.max_drawdown_pct)} valueClassName="text-bad" />
            <StatTile label="Aciertos" value={m!.win_rate !== null ? pct(m!.win_rate) : "—"} />
            <StatTile label="Sharpe" value={m!.sharpe !== null ? num(m!.sharpe, 2) : "—"} sub="por operación" />
            <StatTile label="Sortino" value={m!.sortino !== null ? num(m!.sortino, 2) : "—"} sub="por operación" />
            <StatTile label="Calmar" value={m!.calmar !== null ? num(m!.calmar, 2) : "—"} sub="retorno / drawdown" />
          </div>

          {thin && (
            <div className="rounded-lg border border-warn bg-warn-soft px-4 py-3 text-sm text-warn">
              {m!.closed_trades} operaciones cerradas. Debajo de {MIN_TRADES_FOR_CONFIDENCE} estas métricas no distinguen
              habilidad de suerte — tomalas como una señal de que vale la pena mirar más, no como un resultado.
            </div>
          )}

          <Card>
            <SectionLabel>
              {run.symbol} · {run.strategy} · {run.period}
            </SectionLabel>
            <BacktestChart run={run} />
            <div className="mt-2 text-xs text-muted">▲ entrada · ▼ salida (verde ganadora, roja perdedora)</div>
          </Card>

          <div className="grid grid-cols-1 gap-4 lg:grid-cols-2 xl:grid-cols-3">
            <Card className="xl:col-span-1 lg:col-span-2">
              <SectionLabel>Curva de capital</SectionLabel>
              <EquityCurve curve={run.equity_curve} />
              <div className="mt-2 text-xs text-muted">Avanza por operación cerrada, no por tiempo.</div>
            </Card>
            <Card>
              <SectionLabel>Lo que el retorno total esconde</SectionLabel>
              {(() => {
                const a = run.analytics;
                const rows: [string, string, string][] = [
                  ["Ganadoras / perdedoras", `${a.wins ?? 0} / ${a.losses ?? 0}`, ""],
                  ["Ganancia promedio", a.avg_win_pct !== null && a.avg_win_pct !== undefined ? pct(a.avg_win_pct, { showSign: true }) : "—", "text-accent"],
                  ["Pérdida promedio", a.avg_loss_pct !== null && a.avg_loss_pct !== undefined ? pct(a.avg_loss_pct, { showSign: true }) : "—", "text-bad"],
                  ["Mejor / peor", `${a.best_pct !== null && a.best_pct !== undefined ? pct(a.best_pct, { showSign: true }) : "—"} / ${a.worst_pct !== null && a.worst_pct !== undefined ? pct(a.worst_pct, { showSign: true }) : "—"}`, ""],
                  ["Factor de ganancia", a.profit_factor !== null && a.profit_factor !== undefined ? num(a.profit_factor, 2) : "—", (a.profit_factor ?? 0) >= 1 ? "text-accent" : "text-bad"],
                  ["Expectativa por operación", a.expectancy_pct !== null && a.expectancy_pct !== undefined ? pct(a.expectancy_pct, { showSign: true }) : "—", pnlClass(a.expectancy_pct)],
                  ["Racha máx. ganadora", `${a.max_win_streak ?? 0}`, ""],
                  ["Racha máx. perdedora", `${a.max_loss_streak ?? 0}`, "text-bad"],
                  ["Barras promedio en posición", a.avg_bars_held !== null && a.avg_bars_held !== undefined ? num(a.avg_bars_held, 1) : "—", ""],
                ];
                return (
                  <div className="flex flex-col gap-1.5">
                    {rows.map(([label, value, cls]) => (
                      <div key={label} className="flex items-center justify-between gap-3 border-b border-border py-1.5 last:border-0">
                        <span className="text-sm text-muted">{label}</span>
                        <span className={`font-mono text-sm ${cls}`}>{value}</span>
                      </div>
                    ))}
                  </div>
                );
              })()}
              <div className="mt-3 text-xs text-muted">
                Factor de ganancia debajo de 1 = pierde plata aunque acierte más veces de las que falla.
              </div>
            </Card>

            <Card>
              <SectionLabel>Cómo salió de cada operación</SectionLabel>
              {(() => {
                const by: Record<string, { n: number; pnl: number }> = {};
                for (const t of run.trades) {
                  const k = t.exit_reason ?? "abierta";
                  by[k] = by[k] ?? { n: 0, pnl: 0 };
                  by[k].n += 1;
                  by[k].pnl += t.pnl_pct ?? 0;
                }
                const LABELS: Record<string, string> = {
                  target: "Llegó al objetivo",
                  stop_loss: "Tocó el stop",
                  end_of_window: "Abierta al final",
                  abierta: "Sin cerrar",
                };
                return (
                  <div className="flex flex-col gap-2">
                    {Object.entries(by).map(([k, v]) => (
                      <div key={k} className="flex items-center justify-between gap-3 rounded-md border border-border bg-card-2 px-3 py-2">
                        <span className="text-sm">{LABELS[k] ?? k}</span>
                        <span className="font-mono text-sm">
                          {v.n}× <span className={pnlClass(v.pnl)}>{pct(v.pnl, { showSign: true })}</span>
                        </span>
                      </div>
                    ))}
                  </div>
                );
              })()}
            </Card>
          </div>

          <Card>
            <SectionLabel>Operaciones</SectionLabel>
            {run.trades.length === 0 ? (
              <EmptyState>La estrategia no disparó ninguna vez en este período.</EmptyState>
            ) : (
              <Table>
                <thead>
                  <tr>
                    <Th>Entrada</Th>
                    <Th align="right">Precio</Th>
                    <Th>Salida</Th>
                    <Th align="right">Precio</Th>
                    <Th>Motivo</Th>
                    <Th align="right">Resultado</Th>
                  </tr>
                </thead>
                <tbody>
                  {run.trades.map((t, i) => (
                    <tr key={i}>
                      <Td className="font-mono">{t.entry_time ? new Date(t.entry_time * 1000).toLocaleDateString("es-AR") : "—"}</Td>
                      <Td align="right" className="font-mono">{num(t.entry_price, 2)}</Td>
                      <Td className="font-mono">{t.exit_time ? new Date(t.exit_time * 1000).toLocaleDateString("es-AR") : "—"}</Td>
                      <Td align="right" className="font-mono">{t.exit_price !== null ? num(t.exit_price, 2) : "—"}</Td>
                      <Td className="font-mono text-muted">{t.exit_reason ?? "abierta"}</Td>
                      <Td align="right" className={`font-mono ${pnlClass(t.pnl_pct)}`}>
                        {t.pnl_pct !== null ? pct(t.pnl_pct, { showSign: true }) : "—"}
                      </Td>
                    </tr>
                  ))}
                </tbody>
              </Table>
            )}
          </Card>
        </>
      )}
      <StrategyBuilder key={builderKey} onChanged={() => setBuilderKey((k) => k + 1)} />
    </div>
  );
}
