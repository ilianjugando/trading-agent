import { useEffect, useState } from "react";
import { api, type TaskStatusResponse } from "../api/client";

type Health = "on" | "off" | "mixed" | "unknown";

function health(tasks: TaskStatusResponse["tasks"] | null): Health {
  if (!tasks) return "unknown";
  const values = Object.values(tasks);
  if (!values.length) return "unknown";
  if (values.every((v) => v === "Ready" || v === "Running")) return "on";
  if (values.every((v) => v === "Disabled")) return "off";
  return "mixed";
}

const DOT_CLASS: Record<Health, string> = {
  on: "bg-accent shadow-[0_0_6px_1px_var(--accent)]",
  off: "bg-bad",
  mixed: "bg-warn shadow-[0_0_6px_1px_var(--warn)]",
  unknown: "bg-muted",
};

const LABEL: Record<Health, string> = {
  on: "Bot activo",
  off: "Bot detenido",
  mixed: "Estado mixto",
  unknown: "Verificando…",
};

/** Control real de inicio/parada -- prende o pausa las 3 tareas
 * programadas de Windows via el backend (schtasks). No es una decision
 * de trading: no tiene ningun camino hacia colocar, dimensionar o anular
 * una operacion, eso vive solo en execution/orchestrator.py. */
export function BotControl() {
  const [tasks, setTasks] = useState<TaskStatusResponse["tasks"] | null>(null);
  const [busy, setBusy] = useState(false);

  async function refresh() {
    try {
      const res = await api.botStatus();
      setTasks(res.tasks);
    } catch {
      setTasks(null);
    }
  }

  useEffect(() => {
    refresh();
    const id = setInterval(refresh, 5000);
    return () => clearInterval(id);
  }, []);

  async function act(action: "start" | "stop") {
    const verb = action === "start" ? "Iniciar" : "Detener";
    const msg =
      action === "start"
        ? "¿Iniciar el bot? Reactiva las 3 tareas programadas (acciones, crypto, watchlist)."
        : "¿Detener el bot? Pausa las 3 tareas programadas hasta que lo reinicies.";
    if (!window.confirm(`${verb}: ${msg}`)) return;

    setBusy(true);
    try {
      const res = action === "start" ? await api.botStart() : await api.botStop();
      setTasks(res.tasks);
    } finally {
      setBusy(false);
    }
  }

  const h = health(tasks);

  return (
    <div className="flex items-center gap-2.5 text-sm text-muted">
      <span className={`h-2 w-2 flex-none rounded-full ${DOT_CLASS[h]}`} aria-hidden />
      <span>{LABEL[h]}</span>
      {h !== "on" && (
        <button
          type="button"
          onClick={() => act("start")}
          disabled={busy}
          className="cursor-pointer rounded-md border border-accent px-3.5 py-1.5 font-mono text-xs font-semibold text-accent transition-colors hover:bg-accent-soft disabled:cursor-default disabled:opacity-50"
        >
          Iniciar
        </button>
      )}
      {h !== "off" && (
        <button
          type="button"
          onClick={() => act("stop")}
          disabled={busy}
          className="cursor-pointer rounded-md border border-bad px-3.5 py-1.5 font-mono text-xs font-semibold text-bad transition-colors hover:bg-bad-soft disabled:cursor-default disabled:opacity-50"
        >
          Detener
        </button>
      )}
    </div>
  );
}
