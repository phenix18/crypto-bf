import { defineConfig, loadEnv } from 'vite'
import react from '@vitejs/plugin-react'

// The SPA calls the backend through relative `/api` paths (see src/appShared.tsx).
// In production a reverse proxy serves both on the same origin; in development we
// proxy to the local FastAPI process instead, so `npm run dev` works out of the box.
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), '')
  const apiTarget = env.VITE_API_PROXY_TARGET || 'http://127.0.0.1:8000'

  return {
    plugins: [react()],
    server: {
      port: Number(env.VITE_DEV_PORT || 3000),
      proxy: {
        '/api': { target: apiTarget, changeOrigin: true },
        '/SKILL.md': { target: apiTarget, changeOrigin: true },
        '/skill': { target: apiTarget, changeOrigin: true },
      },
    },
  }
})
