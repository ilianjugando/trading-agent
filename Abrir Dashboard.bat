@echo off
cd /d "%~dp0"

if not exist "dashboard-web\dist\index.html" (
  echo Compilando el frontend por primera vez...
  pushd dashboard-web
  call npm install
  call npm run build
  popd
)

start "" http://127.0.0.1:8787/
".venv\Scripts\python.exe" -m uvicorn api.app:app --port 8787
