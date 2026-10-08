import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

const apiKey = process.env.SCRAPER_API_KEY

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/api/, ''),
        headers: apiKey ? { 'X-API-Key': apiKey } : undefined,
      },
    },
  },
})
