# dashboard-web

Frontend del dashboard del trading bot. TypeScript + React + Vite, consumiendo
la API tipada en `../api/` (FastAPI). Ver `.claude/agents/dashboard-engineer.md`
en la raíz del repo para las convenciones completas (tokens de diseño,
disciplina de verificación con screenshot, etc.).

## Desarrollo

Con el backend corriendo (`.venv\Scripts\python -m uvicorn api.app:app --port 8787`
desde la raíz del repo):

```bash
npm install
npm run dev
```

Abre en `http://localhost:5173` con hot-reload. El proxy de Vite (`vite.config.ts`)
reenvía `/data`, `/bot-status`, `/bot-start`, `/bot-stop` al backend en el
puerto 8787 — no hace falta configurar CORS.

## Regenerar los tipos después de cambiar el contrato del backend

Si tocaste algún modelo en `../api/schemas.py`, los tipos de TypeScript en
`src/api/schema.ts` quedan desactualizados hasta que se regeneren (son
generados, nunca se editan a mano):

```bash
npm run gen-types
```

Requiere el backend corriendo en `127.0.0.1:8787` — lee su OpenAPI spec en vivo.

## Build de producción

```bash
npm run build
```

Genera `dist/`, que `api/app.py` sirve directo desde el mismo proceso de
FastAPI en `http://127.0.0.1:8787/` — un solo servidor para todo, sin
Node.js corriendo en paralelo. `Abrir Dashboard.bat` en la raíz del repo
hace este build automáticamente la primera vez.
