import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// 端口统一在 .env 中配置，由 start-dev.sh 注入环境变量
const backendPort = process.env.BACKEND_PORT || '8000'
const frontendPort = process.env.FRONTEND_PORT || '3000'

export default defineConfig({
  plugins: [react()],
  server: {
    port: parseInt(frontendPort),
    host: '0.0.0.0',
    proxy: {
      '/api': {
        target: `http://localhost:${backendPort}`,
        changeOrigin: true,
      },
    },
  },
  build: {
    outDir: 'dist',
  },
})
