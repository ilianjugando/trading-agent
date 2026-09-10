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
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from api import bot_control, data
from api.schemas import DashboardData, TaskStatusResponse

app = FastAPI(title="Trading Agent API", version="1.0.0")

_DIST_DIR = Path(__file__).resolve().parent.parent / "dashboard-web" / "dist"


@app.get("/data", response_model=DashboardData)
def get_data() -> dict:
    return data.build_data()


@app.get("/bot-status", response_model=TaskStatusResponse)
def get_bot_status() -> dict:
    return {"tasks": bot_control.task_statuses()}


@app.post("/bot-start", response_model=TaskStatusResponse)
def post_bot_start() -> dict:
    return {"tasks": bot_control.set_tasks_enabled(True)}


@app.post("/bot-stop", response_model=TaskStatusResponse)
def post_bot_stop() -> dict:
    return {"tasks": bot_control.set_tasks_enabled(False)}


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
