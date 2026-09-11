/** Mini-curva de precio en SVG puro.
 *
 * A mano y no con lightweight-charts a proposito: una instancia de chart
 * por fila de una tabla de 20 posiciones es canvas de sobra para dibujar
 * 40 puntos sin ejes. Son 12 lineas de SVG. */
export function Sparkline({ points, width = 72, height = 22 }: { points: number[]; width?: number; height?: number }) {
  if (!points || points.length < 2) return <span className="text-muted">—</span>;

  const min = Math.min(...points);
  const max = Math.max(...points);
  const span = max - min || 1;
  const step = width / (points.length - 1);
  const d = points
    .map((p, i) => `${i === 0 ? "M" : "L"}${(i * step).toFixed(1)},${(height - ((p - min) / span) * height).toFixed(1)}`)
    .join(" ");

  const up = points[points.length - 1] >= points[0];
  return (
    <svg width={width} height={height} className="overflow-visible" aria-hidden>
      <path d={d} fill="none" stroke={up ? "var(--accent)" : "var(--bad)"} strokeWidth="1.5" strokeLinejoin="round" />
    </svg>
  );
}
