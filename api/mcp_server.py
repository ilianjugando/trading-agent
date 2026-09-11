"""El bot expuesto por MCP, para operarlo desde cualquier cliente de IA
(Claude, ChatGPT, Cursor, Gemini...).

La regla de diseño esta en que NO se expone. Hay herramientas de lectura
para todo, y de escritura SOLO para las dos acciones que van hacia el
lado seguro: detener el bot y accionar el corte de emergencia.

Nunca se expone arrancar el bot, levantar el corte, ni crear una
estrategia (que despues opera sola). La asimetria es deliberada y es la
misma que sostiene el resto del sistema: un modelo puede cerrar riesgo,
nunca abrirlo. Ver risk/kill_switch.py y el P0 del circuit breaker en
execution/orchestrator.py -- un corte que un modelo pudiera levantar no
seria un corte.

Las herramientas llaman a las funciones de Python directamente, sin
pasar por HTTP: el servidor MCP vive en el mismo proceso que la API.
"""
from mcp.server.mcpserver import MCPServer

from api import bot_control, data
from risk import kill_switch

mcp = MCPServer(
    name="Trading Agent",
    instructions=(
        "Bot de trading propio con cuentas en IBKR (acciones) y OKX (cripto). "
        "Podes consultar cartera, posiciones, riesgo, estrategias y correr backtests. "
        "Podes DETENER la operativa, pero no iniciarla ni crear estrategias: eso se hace "
        "desde el dashboard a proposito."
    ),
)


@mcp.tool()
def resumen_de_cartera() -> dict:
    """Estado general: capital, capital desplegado, posiciones abiertas,
    salud de cada pool y alertas de riesgo activas."""
    d = data.build_data()
    return {
        "generado": d["generated_at"],
        "portafolio": d["portfolio"],
        "rendimiento": d["performance"],
        "salud_por_pool": d["pools"],
        "alertas_de_riesgo": d["risk"]["alerts"],
        "circuit_breakers": d["breakers"],
        "posiciones_abiertas": len(d["live_positions"]),
    }


@mcp.tool()
def posiciones_abiertas() -> list[dict]:
    """Cada posicion abierta con su precio de entrada, precio actual,
    stop, valor de mercado y ganancia/perdida."""
    return [
        {k: p[k] for k in ("pool", "symbol", "entry_price", "current_price", "qty",
                           "stop", "market_value", "pnl_usd", "pnl_pct", "bucket")}
        for p in data.build_data()["live_positions"]
    ]


@mcp.tool()
def actividad_reciente(limite: int = 25) -> list[dict]:
    """Ultimas decisiones del bot: que compro, que salteo y por que."""
    decisions = data.build_data()["recent_decisions"][-limite:]
    return [
        {
            "hora": d.get("timestamp"),
            "pool": d.get("pool"),
            "simbolo": d.get("symbol") or (d.get("signal") or {}).get("symbol"),
            "resultado": d.get("result"),
            "motivo": d.get("reason") or d.get("detail"),
        }
        for d in decisions
    ]


@mcp.tool()
def correr_backtest(simbolo: str, estrategia: str = "trend_follow", periodo: str = "2y") -> dict:
    """Backtestea una estrategia sobre un simbolo con historia real.
    Devuelve retorno, drawdown, aciertos, Sharpe, Sortino y Calmar.

    Las metricas son POR OPERACION, no anualizadas, y con menos de 20
    operaciones no distinguen habilidad de suerte."""
    r = data.backtest_payload(simbolo.upper().strip(), estrategia, periodo)
    return {
        "simbolo": r["symbol"],
        "estrategia": r["strategy"],
        "periodo": r["period"],
        "metricas": r["metrics"],
        "estrategias_disponibles": r["available_strategies"],
        "advertencia": (
            "menos de 20 operaciones: muestra insuficiente para concluir"
            if r["metrics"]["closed_trades"] < 20 else None
        ),
    }


@mcp.tool()
def listar_estrategias() -> dict:
    """Estrategias disponibles: las que trae el bot y las propias."""
    from signals.custom import load_all
    from signals.strategies import ALL_STRATEGIES

    propias = load_all(data.STATE_DIR)
    return {
        "del_bot": sorted(ALL_STRATEGIES),
        "propias": [
            {"nombre": s.name, "descripcion": s.description,
             "condiciones": [f"{r.indicator} {r.op} {r.value}" for r in s.entry]}
            for s in propias.values()
        ],
    }


@mcp.tool()
def estado_del_corte() -> dict:
    """Si el corte de emergencia esta accionado, y por que."""
    return {
        "acciones_bloqueadas": kill_switch.blocked_reason(data.STATE_DIR, "stocks"),
        "cripto_bloqueado": kill_switch.blocked_reason(data.STATE_DIR, "crypto"),
        "tareas_programadas": bot_control.task_statuses(),
    }


@mcp.tool()
def accionar_corte_de_emergencia(motivo: str, pools: list[str] | None = None) -> dict:
    """Corta la apertura de posiciones NUEVAS, en el acto y aunque haya un
    ciclo corriendo. Las salidas (stop-loss) siguen ejecutandose: un corte
    frena riesgo nuevo, nunca la reduccion del riesgo ya tomado.

    `pools` puede ser ["crypto"] o ["stocks"] para cortar solo uno.
    Levantarlo se hace a mano desde el dashboard, no desde aca."""
    kill_switch.engage(data.STATE_DIR, reason=f"via MCP: {motivo}", pools=pools)
    return estado_del_corte()


@mcp.tool()
def detener_el_bot() -> dict:
    """Deshabilita las tareas programadas: no arrancan mas ciclos nuevos.
    No cancela ordenes ya enviadas. Volver a arrancarlo se hace a mano."""
    return {"tareas": bot_control.set_tasks_enabled(False)}
