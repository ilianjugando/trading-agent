"""API tipada para el dashboard, mas el propio frontend ya compilado.

Reemplaza al http.server + json.dumps a mano del dashboard.py anterior
-- misma logica (api/data.py, movida tal cual), seam distinto: FastAPI
valida contra api/schemas.py y genera el OpenAPI spec del que
dashboard-web/ deriva sus tipos de TypeScript.

Un solo runtime, un solo proceso, un solo puerto -- igual de simple que
abrir el dashboard.py anterior. Se evaluo agregar un backend en Node.js
para la capa de API y se descarto: los datos nacen en Python de
cualquier forma (logs, SQLite, JSON de estado), asi que Node no habria
dado tipado de punta a punta -- solo mueve el limite sin tipar de
Python->React a Python->Node, y agrega un segundo proceso que mantener
vivo en Windows. FastAPI + Pydantic + openapi-typescript da el mismo
contrato tipado en React con un solo runtime.

    uvicorn api.app:app --port 8787

Sirve en http://127.0.0.1:8787/ tanto el frontend compilado
(dashboard-web/dist/, si existe -- `npm run build` dentro de
dashboard-web/) como las rutas /data, /bot-status, /bot-start,
/bot-stop. Mismo alcance que el dashboard anterior: 127.0.0.1
solamente, nunca expuesto a la red. Durante `npm run dev` (puerto 5173)
el proxy de Vite habla directo con este servidor, asi que no hace falta
CORS en ningun caso.
"""
import time
from contextlib import asynccontextmanager
from dataclasses import asdict
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from api import bot_control, data
from api.mcp_server import mcp as _mcp
from api.schemas import BacktestRun, CustomStrategy, CustomStrategyList, DashboardData, TranslatedStrategy, TranslateRequest, KillSwitchRequest, KillSwitchStatus, MarketRadar, TaskStatusResponse
from risk import kill_switch
from signals import market_radar

# El servidor MCP vive en el mismo proceso: es lo que hace que el bot
# aparezca como herramientas dentro de Claude, ChatGPT, Cursor o Gemini.
# Ver api/mcp_server.py para que se expone y, sobre todo, que NO.
#
# streamable_http_path="/" para que la URL final sea /mcp y no /mcp/mcp.
# stateless porque cada herramienta es una consulta independiente: no hay
# conversacion que sostener entre llamadas, y sin sesion el endpoint
# sobrevive a que el cliente se reconecte.
_mcp_app = _mcp.streamable_http_app(streamable_http_path="/", stateless_http=True, json_response=True)


@asynccontextmanager
async def _lifespan(_app: FastAPI):
    # Sin esto el sub-app monta pero explota en la primera llamada con
    # "Task group is not initialized": FastAPI no corre el lifespan de un
    # app montado, hay que encadenarlo a mano.
    async with _mcp_app.router.lifespan_context(_mcp_app):
        yield


app = FastAPI(title="Trading Agent API", version="1.0.0", lifespan=_lifespan)
app.mount("/mcp", _mcp_app)


# Starlette monta en "/mcp/..." y no matchea "/mcp" pelado, que es
# justamente como la mayoria de los clientes configuran la URL. Sin esto
# el catch-all del frontend lo agarra y devuelve 405.
@app.api_route("/mcp", methods=["GET", "POST", "DELETE"], include_in_schema=False)
async def _mcp_no_trailing_slash(request: Request) -> Response:
    return await _mcp_app(request.scope | {"path": "/", "raw_path": b"/"}, request.receive, request._send)

_DIST_DIR = Path(__file__).resolve().parent.parent / "dashboard-web" / "dist"

# El radar de mercado pega contra la API interna del screener de
# TradingView (no oficial, ver signals/market_radar.py) -- un cache de
# 60s evita golpearla en cada poll del frontend y evita el riesgo de
# rate-limit si dos pestañas del dashboard estan abiertas a la vez.
_RADAR_CACHE_SECONDS = 60
_radar_cache: dict = {"ts": 0.0, "data": None}


@app.get("/data", response_model=DashboardData, operation_id="estado_del_bot")
def get_data() -> dict:
    return data.build_data()


@app.get("/bot-status", response_model=TaskStatusResponse, operation_id="estado_de_las_tareas")
def get_bot_status() -> dict:
    return {"tasks": bot_control.task_statuses()}


@app.post("/bot-start", response_model=TaskStatusResponse)
def post_bot_start() -> dict:
    return {"tasks": bot_control.set_tasks_enabled(True)}


@app.post("/bot-stop", response_model=TaskStatusResponse, operation_id="detener_el_bot")
def post_bot_stop() -> dict:
    return {"tasks": bot_control.set_tasks_enabled(False)}


@app.get("/kill-switch", response_model=KillSwitchStatus, operation_id="estado_del_corte")
def get_kill_switch() -> dict:
    return _kill_switch_status()


@app.post("/kill-switch/engage", response_model=KillSwitchStatus, operation_id="accionar_el_corte")
def post_kill_switch_engage(body: KillSwitchRequest | None = None) -> dict:
    """Corta la apertura de posiciones NUEVAS. Las salidas (stop-loss)
    siguen ejecutandose siempre -- ver risk/kill_switch.py."""
    reason = (body.reason if body else None) or "accionado desde el dashboard"
    kill_switch.engage(data.STATE_DIR, reason=reason, pools=body.pools if body else None)
    return _kill_switch_status()


@app.post("/kill-switch/release", response_model=KillSwitchStatus)
def post_kill_switch_release() -> dict:
    kill_switch.release(data.STATE_DIR)
    return _kill_switch_status()


def _kill_switch_status() -> dict:
    return {
        "stocks_blocked": kill_switch.blocked_reason(data.STATE_DIR, "stocks"),
        "crypto_blocked": kill_switch.blocked_reason(data.STATE_DIR, "crypto"),
    }


@app.get("/backtest", response_model=BacktestRun, operation_id="backtest")
def get_backtest(symbol: str = "SPY", strategy: str = "trend_follow", period: str = "2y") -> dict:
    """Replay de una estrategia sobre historia real. Sin LLM y sin ordenes."""
    try:
        return data.backtest_payload(symbol.upper().strip(), strategy, period)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@app.get("/strategies/custom", response_model=CustomStrategyList, operation_id="listar_estrategias")
def get_custom_strategies() -> dict:
    from signals.custom import INDICATORS, OPS, load_all

    specs = load_all(data.STATE_DIR)
    return {
        "strategies": [{"name": s.name, "entry": [asdict(r) for r in s.entry], "description": s.description}
                       for s in specs.values()],
        "available_indicators": sorted(INDICATORS),
        "available_operators": sorted(OPS),
    }


@app.post("/strategies/custom", response_model=CustomStrategyList)
def post_custom_strategy(body: CustomStrategy) -> dict:
    from signals.custom import Rule, StrategySpec, save

    try:
        save(data.STATE_DIR, StrategySpec(
            name=body.name,
            entry=[Rule(r.indicator, r.op, r.value) for r in body.entry],
            description=body.description,
        ))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return get_custom_strategies()


@app.delete("/strategies/custom/{name}", response_model=CustomStrategyList)
def delete_custom_strategy(name: str) -> dict:
    from signals.custom import delete

    if not delete(data.STATE_DIR, name):
        raise HTTPException(status_code=404, detail=f"no existe la estrategia {name!r}")
    return get_custom_strategies()


@app.post("/strategies/translate", response_model=TranslatedStrategy)
def post_translate_strategy(body: TranslateRequest) -> dict:
    """Descripcion en castellano -> reglas propuestas. NO guarda nada: lo
    que devuelve el modelo es una propuesta para que el usuario la revise
    antes de activarla."""
    from config.settings import load_settings
    from signals.custom import from_description

    settings = load_settings()
    if not settings.gemini_api_key:
        raise HTTPException(status_code=503, detail="falta GEMINI_API_KEY en .env")
    try:
        return from_description(body.description, settings.gemini_api_key)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"el modelo no pudo traducirlo: {e}") from e


@app.get("/market-radar", response_model=MarketRadar, operation_id="radar_de_mercado")
def get_market_radar() -> dict:
    """Puramente informativo -- ver signals/market_radar.py. No alimenta
    ninguna decision de compra."""
    from datetime import datetime, timezone

    now = time.monotonic()
    if _radar_cache["data"] is not None and now - _radar_cache["ts"] < _RADAR_CACHE_SECONDS:
        cached = dict(_radar_cache["data"])
        cached["generated_at"] = datetime.now(timezone.utc).isoformat()
        return cached

    result = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "cex_movers": [asdict(m) for m in market_radar.cex_movers()],
        "dex_movers": [asdict(m) for m in market_radar.dex_movers()],
    }
    _radar_cache["ts"], _radar_cache["data"] = now, result
    return result



# Deliberadamente NO se monta StaticFiles en "/": un Mount ahi es un
# catch-all que en Starlette siempre produce un match COMPLETO para
# cualquier path, sin importar el orden de registro -- eso se probo en
# vivo con un test real (test_bot_start_and_stop_routes_are_post_only):
# un GET a /bot-start (que solo acepta POST) empezaba a devolver 404 en
# vez de 405, porque el mount interceptaba la request antes de que el
# router notara "el path existe, el metodo no". Por eso el mount de
# assets va en su propio prefijo, que nunca choca con una ruta de API, y
# los archivos sueltos de la raiz (index.html, y lo que Vite copie desde
# dashboard-web/public/) se sirven con rutas explicitas.
#
# Si dashboard-web/ nunca se compilo (instalacion fresca, o alguien solo
# quiere pegarle a la API), el servidor igual arranca -- "/" simplemente
# no sirve nada hasta que exista dist/, en vez de que todo el proceso
# falle por un directorio ausente.
if _DIST_DIR.exists():
    app.mount("/assets", StaticFiles(directory=_DIST_DIR / "assets"), name="assets")

    @app.get("/", include_in_schema=False)
    def index() -> FileResponse:
        return FileResponse(_DIST_DIR / "index.html")

    # Cualquier archivo que Vite copie tal cual desde dashboard-web/public/
    # (favicon.svg, icons.svg...) sale por esta unica ruta generica en vez
    # de una por archivo -- basta con que exista en dist/ para servirse.
    @app.get("/{filename}", include_in_schema=False)
    def public_file(filename: str) -> FileResponse:
        path = _DIST_DIR / filename
        if not path.is_file():
            return FileResponse(_DIST_DIR / "index.html")
        return FileResponse(path)
