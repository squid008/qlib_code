import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// Vite 开发服务器配置
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      // 将 /api 请求代理到 FastAPI 后端
      '/api': {
        target: 'http://localhost:8001',
        changeOrigin: true,
      },
    },
  },
})
