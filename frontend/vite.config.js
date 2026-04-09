import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      // Proxy /api/* → FastAPI on :8000 (avoids CORS in dev)
      '/api': 'http://localhost:8000',
    },
  },
})
