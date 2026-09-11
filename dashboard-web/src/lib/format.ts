export function usd(value: number | null | undefined, opts: { showSign?: boolean } = {}): string {
  if (value === null || value === undefined) return "N/A";
  const sign = opts.showSign && value > 0 ? "+" : "";
  // Los precios de cripto suelen ser sub-dolar: redondear a entero los
  // mostraba todos como "US$ 0". Se usan tantos decimales como haga falta
  // para que el numero signifique algo, y ninguno de mas en los montos
  // grandes, donde los centavos son ruido.
  const abs = Math.abs(value);
  const digits = abs === 0 || abs >= 100 ? 0 : abs >= 1 ? 2 : abs >= 0.01 ? 4 : 8;
  return sign + value.toLocaleString("es-AR", {
    style: "currency",
    currency: "USD",
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  });
}

export function pct(value: number | null | undefined, opts: { showSign?: boolean } = {}): string {
  if (value === null || value === undefined) return "N/A";
  const sign = opts.showSign && value > 0 ? "+" : "";
  return `${sign}${value.toFixed(2)}%`;
}

export function num(value: number | null | undefined, digits = 1): string {
  if (value === null || value === undefined) return "N/A";
  return value.toLocaleString("es-AR", { maximumFractionDigits: digits });
}

/** P&L: verde si gana, rojo si pierde, muted si no hay dato -- nunca el
 * color de acento de marca (--glow), que es identidad de sistema, no
 * dinero. */
export function pnlClass(value: number | null | undefined): string {
  if (value === null || value === undefined) return "text-muted";
  return value >= 0 ? "text-accent" : "text-bad";
}

export function relativeTime(minutes: number | null | undefined): string {
  if (minutes === null || minutes === undefined) return "N/A";
  if (minutes < 1) return "hace instantes";
  if (minutes < 60) return `hace ${Math.round(minutes)} min`;
  const hours = minutes / 60;
  if (hours < 24) return `hace ${hours.toFixed(1)} h`;
  return `hace ${(hours / 24).toFixed(1)} d`;
}

export const POOL_LABELS: Record<string, string> = {
  stocks: "Acciones · IBKR",
  crypto: "Crypto · OKX",
};

export const BUCKET_LABELS: Record<string, string> = {
  core: "Core",
  momentum: "Momentum",
  moonshot: "Moonshot",
  sin_clasificar: "Sin clasificar",
};
