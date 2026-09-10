import { useEffect, useState } from "react";
import { api, type MarketRadar } from "../api/client";

/** Sondea /market-radar cada 60s -- el backend ya cachea por ese mismo
 * tiempo (pega contra la API interna del screener de TradingView, no
 * conviene golpearla mas seguido que el resto de los datos del bot,
 * que se escriben cada 15-30min). */
export function useMarketRadar() {
  const [radar, setRadar] = useState<MarketRadar | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;

    async function poll() {
      try {
        const next = await api.marketRadar();
        if (!cancelled) {
          setRadar(next);
          setError(null);
        }
      } catch (e) {
        if (!cancelled) setError(e instanceof Error ? e.message : String(e));
      }
    }

    poll();
    const id = setInterval(poll, 60_000);
    return () => {
      cancelled = true;
      clearInterval(id);
    };
  }, []);

  return { radar, error };
}
