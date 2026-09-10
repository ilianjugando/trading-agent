"""Start/stop real de las tres tareas programadas. Logica identica a la
del dashboard.py anterior -- control de proceso, nunca una decision de
trading: no tiene ningun camino hacia colocar, dimensionar o anular una
operacion, eso sigue viviendo exclusivamente en execution/orchestrator.py.
"""
import subprocess

# La unica lista de tareas que start/stop puede tocar -- nunca tomada del
# request, asi que no hay forma de apuntar esto a un nombre de tarea
# arbitrario.
BOT_TASKS = ["TradingAgentPaper", "TradingAgentCrypto", "TradingAgentWatchlist"]


def task_statuses() -> dict[str, str]:
    """Estado real de cada tarea, leido de Task Scheduler -- nunca inferido
    de decisions.log, que solo dice cuando corrio por ultima vez, no si
    esta habilitada para volver a correr."""
    statuses = {}
    for name in BOT_TASKS:
        try:
            result = subprocess.run(
                ["schtasks", "/Query", "/TN", name, "/FO", "LIST"],
                capture_output=True, text=True, timeout=10,
            )
            status = next(
                (line.split(":", 1)[1].strip() for line in result.stdout.splitlines()
                 if line.startswith("Status:")),
                None,
            )
            statuses[name] = status or f"error: {result.stderr.strip() or 'tarea no encontrada'}"
        except Exception as e:
            statuses[name] = f"error: {e}"
    return statuses


def set_tasks_enabled(enabled: bool) -> dict[str, str]:
    """Habilita o deshabilita cada tarea. Best-effort por tarea -- una que
    falla no debe impedir que se intenten las demas."""
    flag = "/ENABLE" if enabled else "/DISABLE"
    results = {}
    for name in BOT_TASKS:
        try:
            subprocess.run(
                ["schtasks", "/Change", "/TN", name, flag],
                capture_output=True, text=True, timeout=10, check=True,
            )
            results[name] = "ok"
        except Exception as e:
            results[name] = f"error: {e}"
    return results
