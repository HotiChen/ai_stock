import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    port: 9429,
    proxy: {
      '/api': 'http://localhost:1234',
      '/ws': { target: 'ws://localhost:1234', ws: true },
    },
  },
})
