import tailwindcss from '@tailwindcss/vite'
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    proxy: {
      // El backend FastAPI corre en 8787 (mismo puerto que el dashboard.py
      // anterior). El proxy evita configurar CORS en dev para cada ruta
      // nueva -- en build de produccion se sirve todo desde el mismo origen.
      '/data': 'http://127.0.0.1:8787',
      '/bot-status': 'http://127.0.0.1:8787',
      '/bot-start': 'http://127.0.0.1:8787',
      '/bot-stop': 'http://127.0.0.1:8787',
    },
  },
})
