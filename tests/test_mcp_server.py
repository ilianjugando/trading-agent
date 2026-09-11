import asyncio

from api.mcp_server import mcp


def _tools():
    return {t.name for t in asyncio.run(mcp.list_tools())}


def test_exposes_the_read_tools():
    names = _tools()
    for expected in ("resumen_de_cartera", "posiciones_abiertas", "correr_backtest",
                     "listar_estrategias", "estado_del_corte", "actividad_reciente"):
        assert expected in names


def test_never_exposes_a_way_to_start_trading():
    """La regla de diseño del modulo: un cliente de IA puede CERRAR riesgo,
    nunca abrirlo. Si alguna vez se agrega una herramienta que arranca el
    bot, levanta el corte o crea una estrategia (que despues opera sola),
    este test tiene que fallar."""
    names = _tools()
    prohibidas = {
        "iniciar_el_bot", "arrancar_el_bot", "bot_start",
        "levantar_el_corte", "liberar_el_corte", "kill_switch_release",
        "crear_estrategia", "guardar_estrategia", "borrar_estrategia",
        "comprar", "vender", "colocar_orden",
    }
    assert not (names & prohibidas), f"herramienta que abre riesgo expuesta por MCP: {names & prohibidas}"


def test_the_only_write_tools_reduce_risk():
    """Whitelist explicita: cualquier herramienta de escritura nueva tiene
    que pasar por acá antes de existir."""
    escritura_permitida = {"accionar_corte_de_emergencia", "detener_el_bot"}
    lectura = {"resumen_de_cartera", "posiciones_abiertas", "actividad_reciente",
               "correr_backtest", "listar_estrategias", "estado_del_corte"}
    assert _tools() == escritura_permitida | lectura


def test_every_tool_is_documented():
    """Sin descripcion, un cliente de IA no sabe cuando usarla."""
    for t in asyncio.run(mcp.list_tools()):
        assert t.description and len(t.description) > 30, f"{t.name} sin documentar"
