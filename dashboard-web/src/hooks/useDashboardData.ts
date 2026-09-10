import { useEffect, useRef, useState } from "react";
import { api, type DashboardData } from "../api/client";

/** Sondea /data cada 5s -- el orchestrator solo escribe cada 15-30min
 * (51KB reales medidos en un dia normal, irrelevante), asi que esto nunca
 * necesito ni justifica reemplazarse por WebSockets/SSE. Evita re-renders
 * cuando el JSON no cambio realmente (mismo criterio que el dashboard
 * anterior: comparar todo menos `generated_at`, que siempre difiere). */
export function useDashboardData() {
  const [data, setData] = useState<DashboardData | null>(null);
  const [error, setError] = useState<string | null>(null);
  const lastSig = useRef<string | null>(null);

  useEffect(() => {
    let cancelled = false;

    async function poll() {
      try {
        const next = await api.data();
        if (cancelled) return;
        const { generated_at: _generated_at, ...rest } = next;
        const sig = JSON.stringify(rest);
        if (sig !== lastSig.current) {
          lastSig.current = sig;
          setData(next);
        } else {
          setData((prev) => (prev ? { ...prev, generated_at: next.generated_at } : next));
        }
        setError(null);
      } catch (e) {
        if (!cancelled) setError(e instanceof Error ? e.message : String(e));
      }
    }

    poll();
    const id = setInterval(poll, 5000);
    return () => {
      cancelled = true;
      clearInterval(id);
    };
  }, []);

  return { data, error };
}
