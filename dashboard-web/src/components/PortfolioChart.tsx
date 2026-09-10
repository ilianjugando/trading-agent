import { AreaSeries, createChart, type IChartApi, type Time } from "lightweight-charts";
import { useEffect, useRef } from "react";
import type { PortfolioPoint } from "../api/client";

/** Lightweight Charts (TradingView) -- recomendado explicitamente por la
 * busqueda de ui-ux-pro-max para series financieras, en vez de una
 * libreria de charting generica: soporta miles de puntos en canvas sin
 * jank y el look es el que un usuario de plataformas de trading ya
 * reconoce. */
export function PortfolioChart({ series }: { series: PortfolioPoint[] }) {
  const containerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);

  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;

    const styles = getComputedStyle(document.documentElement);
    const accent = styles.getPropertyValue("--accent").trim();
    const muted = styles.getPropertyValue("--muted").trim();
    const border = styles.getPropertyValue("--border").trim();
    const fg = styles.getPropertyValue("--fg").trim();

    const chart = createChart(el, {
      width: el.clientWidth,
      height: 260,
      layout: { background: { color: "transparent" }, textColor: muted, fontFamily: "Fira Code, monospace", fontSize: 11 },
      grid: { horzLines: { color: border }, vertLines: { visible: false } },
      rightPriceScale: { borderColor: border },
      timeScale: { borderColor: border, timeVisible: true },
      crosshair: { vertLine: { color: muted, labelBackgroundColor: fg }, horzLine: { color: muted, labelBackgroundColor: fg } },
    });
    chartRef.current = chart;

    const areaSeries = chart.addSeries(AreaSeries, {
      lineColor: accent,
      topColor: `color-mix(in srgb, ${accent} 25%, transparent)`,
      bottomColor: "transparent",
      lineWidth: 2,
      priceFormat: { type: "custom", formatter: (v: number) => `$${Math.round(v).toLocaleString("es-AR")}` },
    });

    areaSeries.setData(
      series.map((p) => ({ time: (new Date(p.timestamp).getTime() / 1000) as Time, value: p.total_value })),
    );
    chart.timeScale().fitContent();

    const resize = () => chart.applyOptions({ width: el.clientWidth });
    window.addEventListener("resize", resize);
    return () => {
      window.removeEventListener("resize", resize);
      chart.remove();
    };
  }, [series]);

  return <div ref={containerRef} className="w-full" />;
}
