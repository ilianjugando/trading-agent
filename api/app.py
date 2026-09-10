"""API tipada para el dashboard. Reemplaza al http.server + json.dumps a
mano del dashboard.py anterior -- misma logica (api/data.py, movida tal
cual), seam distinto: FastAPI valida contra api/schemas.py y genera el
OpenAPI spec del que dashboard-web/ deriva sus tipos de TypeScript.

Corre en un solo runtime (Python). Se evaluo agregar un backend en
Node.js para la nueva capa de API y se descarto: los datos nacen en
Python de cualquier forma (logs, SQLite, JSON de estado), asi que Node
no habria dado tipado de punta a punta -- solo mueve el limite sin tipar
de Python->React a Python->Node, y agrega un segundo proceso que
mantener vivo en Windows. FastAPI + Pydantic + openapi-typescript da el
mismo contrato tipado en React con un solo runtime.

    uvicorn api.app:app --port 8787

Se sirve en 127.0.0.1 solamente -- mismo alcance que el dashboard
anterior, sin exponerse a la red.
"""
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api import bot_control, data
from api.schemas import DashboardData, TaskStatusResponse

app = FastAPI(title="Trading Agent API", version="1.0.0")

# El frontend de Vite corre en un puerto de desarrollo distinto
# (localhost:5173) durante `npm run dev`; en produccion local ambos
# siguen siendo 127.0.0.1, nunca se expone a otro host.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://127.0.0.1:5173", "http://localhost:5173"],
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


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
